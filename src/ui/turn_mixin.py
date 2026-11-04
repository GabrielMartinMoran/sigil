"""Ejecución de un turno del agente: stream de eventos y checkpoints."""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai.capabilities import ProcessEventStream
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from pydantic_ai.usage import UsageLimits
from textual import work

from src.config_provider import ConfigProvider
from src.engine.agents.step_limiter import StepLimiter, StepLimitExceeded
from src.ui.efforts import EFFORTS
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput

CHECKPOINT_INTERVAL = 1.0
"""Segundos mínimos entre checkpoints durante un turno (el guardado final es obligatorio)."""


class TurnMixin:
    """Corre el turno del agente contra la app.

    Espera que la app defina ``_agent``, ``_history``, ``_session``, ``_store``,
    ``_current_run``, ``_last_checkpoint_at`` y ``_update_status``.
    """

    _agent: Any
    _history: list[Any]
    _session: Any
    _store: Any
    _current_run: Any
    _last_checkpoint_at: float

    def model_settings(self) -> dict[str, Any] | None:
        """Settings por request: el esfuerzo de razonamiento configurado."""
        effort = ConfigProvider.REASONING_EFFORT
        if effort in EFFORTS:
            return {'thinking': effort}
        return None

    def checkpoint(self, *, force: bool = False) -> None:
        """Persiste el avance del turno en curso, como mucho una vez por segundo.

        Se llama después de cada iteración del agente y de cada evento del
        stream. Si el proceso muere a mitad de un turno largo, la sesión en
        disco conserva lo completado hasta el último checkpoint. Sólo escribe
        cuando el historial cambió y pasó la ventana de throttle; el guardado
        final del turno es obligatorio y no pasa por acá.
        """
        run = self._current_run
        if run is None:
            return
        messages = run.all_messages()
        if not messages:
            return
        current = self._session.messages
        if len(messages) == len(current) and current and messages[-1] is current[-1]:
            return
        now = time.monotonic()
        if not force and now - self._last_checkpoint_at < CHECKPOINT_INTERVAL:
            return
        self._last_checkpoint_at = now
        self._session.messages = list(messages)
        self._store.save(self._session)
        # La barra se actualiza por paso, no sólo al cerrar el turno.
        self.update_status(messages)
        # La vista de debug también (es un no-op si está cerrada).
        refresh_debug = getattr(self, 'refresh_debug', None)
        if callable(refresh_debug):
            refresh_debug(messages)

    def turn_handler(self, limiter: StepLimiter, chat: ChatLog) -> Any:
        """Handler del stream que alimenta el chat y los checkpoints."""

        async def handler(_ctx: Any, events: Any) -> None:
            async for event in events:
                limiter.count(event)
                # Checkpoint por evento: apenas el run agrega un mensaje al
                # historial (p. ej. el prompt del usuario) queda en disco.
                self.checkpoint()
                await self.dispatch_event(event, chat)

        return handler

    async def dispatch_event(self, event: Any, chat: ChatLog) -> None:
        """Traduce un evento del stream a lo que muestra el chat."""
        if isinstance(event, PartStartEvent):
            if event.index == 0:
                await chat.begin_response()
            part = event.part
            if isinstance(part, ThinkingPart) and part.content:
                await chat.add_thinking(part.content)
            elif isinstance(part, TextPart) and part.content:
                await chat.add_text(part.content)
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, TextPartDelta) and delta.content_delta:
                await chat.add_text(delta.content_delta)
            elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                await chat.add_thinking(delta.content_delta)
        elif isinstance(event, FunctionToolResultEvent):
            ok = getattr(event.part, 'outcome', 'success') == 'success'
            await chat.add_tool_result(event.part.tool_name, event.part.content, ok=ok)

    @work(exclusive=True)
    async def run_turn(self, text: str) -> None:
        """Corre un turno completo, persistiendo el avance sobre la marcha."""
        chat = self.query_one(ChatLog)
        prompt = self.query_one(PromptInput)
        limiter = StepLimiter(ConfigProvider.MAX_STEPS)
        handler = self.turn_handler(limiter, chat)
        prompt.disabled = True
        # `agent.iter` (en vez de `agent.run`) conserva el run accesible: si el
        # turno se aborta por el límite de pasos o por un error, `run.all_messages()`
        # devuelve lo que se alcanzó a completar y la sesión no pierde el contexto.
        #
        # `request_limit=None` desactiva el tope de 50 requests que pydantic-ai
        # aplica por default: el límite de pasos de Sigil (`SIGIL_MAX_STEPS`) ya
        # acota el turno, y el default cortaría antes con un `UsageLimitExceeded`
        # que el usuario no configuró.
        run: Any = None
        history = self._history
        # El primer checkpoint del turno (el prompt del usuario) se guarda ya.
        self._last_checkpoint_at = 0.0
        try:
            async with self._agent.iter(
                text,
                message_history=self._history,
                capabilities=[ProcessEventStream(handler=handler)],
                model_settings=self.model_settings(),
                usage_limits=UsageLimits(request_limit=None),
            ) as run:
                self._current_run = run
                async for _node in run:
                    # Checkpoint por iteración: cada nodo completado (respuesta
                    # del modelo, tanda de tools) queda persistido.
                    self.checkpoint()
            history = run.all_messages()
        except StepLimitExceeded:
            if run is not None:
                history = run.all_messages()
            await chat.add_notice(
                f'Step limit of {ConfigProvider.MAX_STEPS} reached; turn aborted.',
                style='bold red',
            )
        except Exception as exc:  # noqa: BLE001
            if run is not None:
                history = run.all_messages()
            await chat.add_notice(f'Error: {exc}', style='bold red')
        finally:
            self._current_run = None
            prompt.disabled = False
            prompt.focus()
            chat.finalize()

        self._history = history
        self._session.messages = self._history
        self._store.save(self._session)
        self.update_status()
        # La vista de debug muestra el costo del turno recién cerrado.
        refresh_debug = getattr(self, 'refresh_debug', None)
        if callable(refresh_debug):
            refresh_debug()
