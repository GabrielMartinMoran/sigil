import rich
from prompt_toolkit import prompt
from pydantic_ai.messages import ModelMessage

from src.config_provider import ConfigProvider
from src.engine.agents.agent import create_agent
from src.engine.agents.step_limiter import StepLimiter, StepLimitExceeded
from src.engine.sessions.session_store import Session, SessionStore
from src.engine.tools.tool_manager import ToolManager
from src.ui.stream_renderer import StreamRenderer

_NEW_COMMAND = '/new'
_LOAD_COMMAND = '/load'
_COMMANDS_HELP = '[dim]Comandos: /new inicia una sesión nueva, /load carga otra.[/dim]'


def _choose_session(store: SessionStore, current: Session | None) -> Session | None:
    """Lista las sesiones guardadas y deja elegir una por número o id."""
    sessions = store.list_sessions()

    if not sessions:
        rich.print('[yellow]No hay sesiones guardadas para este directorio.[/yellow]')
        return None

    rich.print('[bold]Sesiones guardadas:[/bold]')
    for index, session in enumerate(sessions, start=1):
        marker = ' [green]· actual[/green]' if current is not None and session.id == current.id else ''
        preview = session.preview[:70]
        rich.print(
            f'  [cyan]{index}[/cyan]. [bold]{session.short_id}[/bold] '
            f'[dim]({len(session.messages)} mensajes · {session.updated_at})[/dim]{marker}'
        )
        if preview:
            rich.print(f'      [dim]{preview}[/dim]')

    raw = prompt('Número o id (Enter para cancelar): ').strip()
    if not raw:
        return None

    if raw.isdigit():
        index = int(raw)
        if not 1 <= index <= len(sessions):
            rich.print('[red]Número de sesión inválido.[/red]')
            return None
        return store.load(sessions[index - 1].id)

    return _load_session(store, raw)


def _load_session(store: SessionStore, token: str) -> Session | None:
    """Carga una sesión por id (o prefijo único de id)."""
    matches = store.find(token)

    if not matches:
        rich.print(f'[red]No se encontró ninguna sesión con id "{token}".[/red]')
        return None

    if len(matches) > 1:
        rich.print('[red]El id es ambiguo; usá más caracteres.[/red]')
        return None

    return store.load(matches[0].id)


def _persist(store: SessionStore, session: Session) -> None:
    """Guarda la sesión actual si tiene contenido."""
    if session.messages:
        store.save(session)


def _make_event_handler(limiter: StepLimiter, renderer: StreamRenderer):
    """Handler que cuenta pasos (y puede abortar) y renderiza cada evento."""

    async def _on_event(_ctx, events):
        async for event in events:
            limiter.count(event)
            renderer.render_event(event)

    return _on_event


def run() -> None:
    agent = create_agent()

    tool_manager = ToolManager()
    tool_manager.register_tools(agent)

    store = SessionStore()
    renderer = StreamRenderer()

    # Al iniciar, retomamos la última conversación de este directorio. Si no hay
    # ninguna, arrancamos una sesión nueva.
    session = store.load_last()
    if session is not None:
        rich.print(f'[dim]Retomando sesión [bold]{session.short_id}[/bold] ({len(session.messages)} mensajes).[/dim]')
    else:
        session = store.create_session()
        rich.print('[dim]Nueva sesión creada.[/dim]')

    # Historial de la conversación. Se pasa en cada turno para que el agente
    # conserve el contexto de los mensajes anteriores y de los resultados de
    # tools (llamadas y retornos incluidos en los mensajes de pydantic-ai).
    message_history: list[ModelMessage] = session.messages

    rich.print('[bold magenta]Welcome to Sigil![/bold magenta]')
    rich.print(_COMMANDS_HELP)

    while True:
        user_input = prompt('> ', multiline=True)
        stripped = user_input.strip()

        if not stripped:
            continue

        if stripped.startswith('/'):
            command, _, argument = stripped.partition(' ')
            argument = argument.strip()

            if command == _NEW_COMMAND:
                _persist(store, session)
                session = store.create_session()
                message_history = session.messages
                rich.print(f'[green]Nueva sesión {session.short_id}.[/green]')
                continue

            if command == _LOAD_COMMAND:
                _persist(store, session)
                loaded = _load_session(store, argument) if argument else _choose_session(store, session)

                if loaded is None:
                    continue

                session = loaded
                message_history = session.messages
                rich.print(f'[green]Cargada sesión {session.short_id} ({len(session.messages)} mensajes).[/green]')
                continue

            rich.print(f'[red]Comando desconocido: {command}[/red]')
            rich.print(_COMMANDS_HELP)
            continue

        # `run_sync` corre el loop del agente HASTA EL FINAL: ejecuta todas las
        # tool calls y vuelve a consultar al modelo hasta que no queden tool
        # calls pendientes. El `event_stream_handler` nos permite mostrar el
        # texto y el pensamiento en vivo mientras eso ocurre. De este modo el
        # control sólo vuelve al usuario cuando el agente terminó de verdad, y
        # no en cuanto el modelo emite el primer mensaje de texto.
        #
        # El mismo handler cuenta los pasos del turno (mensajes del modelo +
        # llamadas a tools) y aborta si se supera el límite configurado.
        limiter = StepLimiter(ConfigProvider.MAX_STEPS)
        event_handler = _make_event_handler(limiter, renderer)

        try:
            result = agent.run_sync(
                user_input,
                message_history=message_history,
                event_stream_handler=event_handler,
            )
        except StepLimitExceeded:
            renderer.finish()
            rich.print(
                f'[bold red]Se alcanzó el límite de {ConfigProvider.MAX_STEPS} pasos en este turno; '
                'se abortó la ejecución.[/bold red]'
            )
            # No persistimos el turno abortado: el historial queda consistente
            # (sin tool calls colgadas) y el usuario ya vio la salida en vivo.
            continue

        renderer.finish()

        # Persistimos todo (incluye tool calls y tool returns) para el próximo
        # turno y para poder retomar la conversación más adelante.
        message_history = result.all_messages()
        session.messages = message_history
        store.save(session)
