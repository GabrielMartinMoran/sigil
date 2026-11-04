"""Smoke tests de la app Textual (sin red ni modelo real)."""

import asyncio
import os
import tempfile
from pathlib import Path

# ConfigProvider lee estas variables al importarse; las definimos antes.
os.environ.setdefault('LLM_MODEL', 'test/model')
os.environ.setdefault('OPENROUTER_API_KEY', 'test-key')

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import src.ui.app as appmod
from src.engine.sessions.session_store import SessionStore
from src.engine.tools.tool_registry import ToolLoggerHub, ToolRegistrar
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.prompt_input import PromptInput


async def _fake_fetch(model_id):
    return None


async def _wait_idle(app, pilot, attempts: int = 40) -> None:
    for _ in range(attempts):
        await pilot.pause()
        if not app.query_one(PromptInput).disabled:
            return


def _make_app(agent: Agent, tmp: str, tool_logger: ToolLoggerHub | None = None) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    return SigilApp(agent=agent, store=store, session=session, tool_logger=tool_logger)


def test_app_runs_turn_and_renders_chat(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='respuesta'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'o', 'l', 'a')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                chat = app.query_one(ChatLog)
                assert len(chat.children) >= 2  # mensaje del usuario + respuesta
                assert len(app._session.messages) == 2

    asyncio.run(inner())


def test_command_menu_filters_and_selects(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one(PromptInput).value = ''
                await pilot.press('slash')
                await pilot.pause()

                menu = app.query_one(CommandMenu)
                assert menu.display is True
                assert menu.option_count == 5

                await pilot.press('n')
                await pilot.pause()
                assert menu.option_count == 1
                assert menu.get_option_at_index(0).id == '/new'

                await pilot.press('enter')
                await pilot.pause()
                assert menu.display is False

    asyncio.run(inner())


def _chat_texts(app) -> list[str]:
    """Texto plano de cada mensaje del chat."""
    texts = []
    for child in app.query_one(ChatLog).children:
        try:
            rendered = child.render()
        except Exception:  # noqa: BLE001, S112 - widgets sin render propio
            continue
        plain = getattr(rendered, 'plain', None)
        if isinstance(plain, str):
            texts.append(plain)
    return texts


def test_mcp_command_without_servers_explains_how_to_configure(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('/')
                await pilot.press('m', 'c', 'p')
                await pilot.press('enter')
                await pilot.pause()

                assert any('mcp_servers' in text for text in _chat_texts(app))

    asyncio.run(inner())


def test_startup_notices_are_rendered(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(
                agent=agent,
                store=store,
                session=store.create_session(),
                startup_notices=('mcp_servers es inválido',),
            )
            async with app.run_test() as pilot:
                await pilot.pause()
                assert any('mcp_servers es inválido' in text for text in _chat_texts(app))

    asyncio.run(inner())


def test_tool_call_and_result_are_rendered(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            tool_logger = ToolLoggerHub()
            agent = Agent(TestModel(call_tools=['ping']), instructions='x')

            @ToolRegistrar(agent, tool_logger).tool
            def ping() -> str:
                return 'pong'

            app = _make_app(agent, tmp, tool_logger)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                widgets = list(app.query_one(ChatLog).children)
                class_names = {name for widget in widgets for name in widget.classes}
                assert 'tool-call' in class_names
                assert 'tool-result' in class_names
                assert any('ping' in str(widget.render()) for widget in widgets)
                assert len(app._session.messages) >= 3

    asyncio.run(inner())


def test_tool_error_is_rendered_as_notice(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            tool_logger = ToolLoggerHub()
            agent = Agent(TestModel(call_tools=['explode']), instructions='x')

            @ToolRegistrar(agent, tool_logger).tool
            def explode() -> str:
                raise ValueError('kaboom')

            app = _make_app(agent, tmp, tool_logger)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                texts = [str(widget.render()) for widget in app.query_one(ChatLog).children]
                assert any('kaboom' in text for text in texts)

    asyncio.run(inner())


def test_autoscroll_only_when_at_bottom(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(Agent(TestModel(), instructions='x'), tmp)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)

                for index in range(60):
                    await chat.add_user(f'mensaje {index}')
                await pilot.pause()
                assert chat.is_vertical_scroll_end

                chat.scroll_home(animate=False)
                await pilot.pause()
                assert not chat.is_vertical_scroll_end
                y = chat.scroll_offset.y

                await chat.add_user('nuevo con scroll arriba')
                await pilot.pause()
                assert chat.scroll_offset.y == y  # no salta al fondo

    asyncio.run(inner())


def test_autoscroll_follows_streaming_growth(monkeypatch):
    """El auto-scroll debe seguir el contenido que crece durante el streaming.

    Regresión: el scroll manual tras cada delta se ejecutaba antes del flush
    diferido del bloque (throttle de 80 ms), así que la vista quedaba corta.
    El anclaje de Textual reajusta el scroll en cada layout.
    """
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(Agent(TestModel(), instructions='x'), tmp)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)

                for index in range(40):
                    await chat.add_user(f'mensaje {index}')
                await pilot.pause()
                assert chat.is_vertical_scroll_end

                # Varios deltas dentro de la ventana del throttle: el contenido
                # real recién crece cuando el timer hace flush.
                await chat.begin_response()
                for _ in range(20):
                    await chat.add_text('linea de respuesta ' * 4)

                # Refresh con el contenido aun sin crecer (el scroll manual
                # agendado se ejecutaria aca, con la altura vieja)...
                await pilot.pause()
                # ...y recien despues el throttle vuelca el texto pendiente.
                await asyncio.sleep(0.15)
                await pilot.pause()

                assert chat.is_vertical_scroll_end

    asyncio.run(inner())


def test_markdown_output_is_rendered_in_app(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='# Encabezado\n\nEsto es **negrita**.'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test(size=(90, 30)) as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                svg = app.export_screenshot()
                assert 'Encabezado' in svg
                assert '# Encabezado' not in svg  # el '#' de markdown no se muestra literal
                assert 'negrita' in svg
                assert '**negrita**' not in svg  # los '**' no se muestran literales

    asyncio.run(inner())


def test_step_limit_abort_preserves_session_and_context(monkeypatch):
    """Regresión: al agotar los pasos de un turno no se pierde la sesión.

    El turno abortado debe conservar lo completado (incluido el mensaje del
    usuario), persistirse en disco y servir de contexto para el turno siguiente.
    """
    from src.config_provider import ConfigProvider

    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)
    monkeypatch.setattr(ConfigProvider, 'MAX_STEPS', 2)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            tool_logger = ToolLoggerHub()
            agent = Agent(TestModel(call_tools=['ping']), instructions='x')

            @ToolRegistrar(agent, tool_logger).tool
            def ping() -> str:
                return 'pong'

            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            app = SigilApp(agent=agent, store=store, session=session, tool_logger=tool_logger)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                # El turno se cortó por el límite, pero no se perdió el contexto.
                assert any('límite' in text for text in _chat_texts(app))
                assert len(app._history) >= 2
                assert len(app._session.messages) == len(app._history)

                # La sesión quedó guardada y aparece en el listado con preview.
                sessions = store.list_sessions()
                saved = next((s for s in sessions if s.id == session.id), None)
                assert saved is not None
                assert saved.preview == 'hi'

                reloaded = store.load(session.id)
                assert reloaded is not None
                assert len(reloaded.messages) == len(app._history)

                # El turno siguiente arranca con todo el contexto anterior.
                prior = len(app._history)
                monkeypatch.setattr(ConfigProvider, 'MAX_STEPS', 20)
                await pilot.press('a', 'g', 'a', 'i', 'n')
                await pilot.press('enter')
                await _wait_idle(app, pilot)
                assert len(app._history) > prior

    asyncio.run(inner())


def test_checkpoint_persists_progress_during_turn(monkeypatch):
    """Regresión: el avance se guarda durante el turno, no sólo al final.

    Si el proceso muere a mitad de un turno largo, la sesión en disco debe
    conservar lo completado hasta el último paso.
    """
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)
    # Sin throttle para que el checkpoint de mitad de turno sea determinista.
    monkeypatch.setattr(appmod, 'CHECKPOINT_INTERVAL', 0.0)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            tool_logger = ToolLoggerHub()
            agent = Agent(TestModel(call_tools=['ping']), instructions='x')
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            seen: list[int] = []

            @ToolRegistrar(agent, tool_logger).tool
            def ping() -> str:
                # La tool corre a mitad del turno: el checkpoint ya tiene que
                # haber persistido el prompt del usuario y la respuesta previa.
                loaded = store.load(session.id)
                seen.append(len(loaded.messages) if loaded is not None else 0)
                return 'pong'

            app = SigilApp(agent=agent, store=store, session=session, tool_logger=tool_logger)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

            assert seen, 'la tool no llegó a ejecutarse'
            assert seen[0] >= 2  # prompt del usuario + respuesta del modelo

    asyncio.run(inner())


def test_checkpoint_throttles_writes_but_force_saves(monkeypatch):
    """El checkpoint se limita a ~1/s; `force` (fin de turno) siempre guarda."""
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    with tempfile.TemporaryDirectory() as tmp:
        store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
        session = store.create_session()
        app = SigilApp(agent=Agent(TestModel(), instructions='x'), store=store, session=session)

        class _FakeRun:
            def __init__(self) -> None:
                self.messages: list = []

            def all_messages(self) -> list:
                return self.messages

        run = _FakeRun()
        app._current_run = run

        run.messages = [ModelRequest(parts=[UserPromptPart(content='uno')])]
        app._checkpoint()
        assert len(store.load(session.id).messages) == 1

        run.messages = [*run.messages, ModelRequest(parts=[UserPromptPart(content='dos')])]
        app._checkpoint()  # dentro de la ventana: no escribe
        assert len(store.load(session.id).messages) == 1

        app._checkpoint(force=True)  # fin de turno: obligatorio
        assert len(store.load(session.id).messages) == 2
