"""Vista de debug del agente: `alt+d` reemplaza el chat por lo que ve el agente."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import ModelMessage
from textual.css.query import NoMatches

from src.config_provider import ConfigProvider
from src.engine.context.meter.debug import DebugReport, build_debug_report
from src.engine.context.meter.request import RequestSnapshot
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.debug_view import AgentDebugView
from src.ui.widgets.prompt_input import PromptInput

_EMPTY_REQUEST = RequestSnapshot()
"""Snapshot cuando todavía no corrió ningún request en la sesión."""

_REFRESH_DELAY = 0.15
"""Segundos de coalescencia. Durante un turno los eventos del stream llegan en
ráfaga y re-proyectar el historial en cada uno es trabajo tirado: el cuerpo se
repinta una vez por ventana y no se pierde ningún estado, porque todo se deriva
del historial."""


class DebugMixin:
    """Alterna entre el chat y la vista de debug del agente.

    Espera que la app defina ``_history``, ``_meter``, ``_context_capability``
    y ``debug_visible``.
    """

    _history: list[ModelMessage]
    _meter: Any
    _context_capability: Any
    debug_visible: bool
    _debug_timer: Any
    _debug_pending: list[ModelMessage] | None

    # --- toggle ----------------------------------------------------------

    def toggle_debug(self) -> None:
        """Muestra u oculta la vista de debug, reemplazando el chat."""
        self.set_debug_visible(not self.debug_visible)

    def set_debug_visible(self, visible: bool) -> None:
        """Fija el estado de la vista de debug y redibuja."""
        self.debug_visible = visible
        try:
            view = self.query_one(AgentDebugView)
            chat = self.query_one(ChatLog)
            prompt = self.query_one(PromptInput)
        except NoMatches:
            return
        view.display = visible
        # Reemplaza, no convive: el espacio es el mismo y la vista es la misma
        # conversación contada desde el otro lado. El input sigue activo y con
        # el foco, a propósito: la vista se refresca con cada paso del turno, así
        # se puede mandar un prompt y ver en vivo qué contexto y qué caché cuesta.
        chat.display = not visible
        if visible:
            self.refresh_debug(immediate=True)
        else:
            self._cancel_debug_refresh()
        prompt.focus()

    # --- reporte ---------------------------------------------------------

    def debug_report(self, messages: Sequence[ModelMessage] | None = None) -> DebugReport:
        """Arma el reporte de debug con el log actual y la proyección del próximo request."""
        history = list(self._history if messages is None else messages)
        capability = self._context_capability
        request: RequestSnapshot | None = None
        view_result = None
        if capability is not None:
            request = capability.last_request
            try:
                # La vista del próximo request, no la del anterior: lo que el
                # agente va a recibir con el historial de ahora.
                view_result = capability.project(history)
            except Exception:  # noqa: BLE001 - el diagnóstico nunca rompe la UI
                view_result = capability.last_view
        return build_debug_report(
            history,
            snapshot=self._meter.snapshot(history),
            request=request or _EMPTY_REQUEST,
            view_result=view_result,
            effort=self.effort_in_effect(),
            model=ConfigProvider.LLM_MODEL,
        )

    def refresh_debug(self, messages: Sequence[ModelMessage] | None = None, *, immediate: bool = False) -> None:
        """Pide un repintado de la vista de debug. No hace nada si está cerrada.

        Sin ``immediate`` el repintado se coalesce: se agenda uno solo por
        ventana y el último historial gana.
        """
        if not self.debug_visible:
            return
        self._debug_pending = list(self._history if messages is None else messages)
        if immediate:
            self._flush_debug()
        elif self._debug_timer is None:
            self._debug_timer = self.set_timer(_REFRESH_DELAY, self._flush_debug)

    def _cancel_debug_refresh(self) -> None:
        """Descarta un repintado agendado: la vista ya no se ve."""
        if self._debug_timer is not None:
            self._debug_timer.stop()
            self._debug_timer = None
        self._debug_pending = None

    def _flush_debug(self) -> None:
        """Pinta el reporte pendiente. Es el callback del timer de coalescencia."""
        self._debug_timer = None
        pending = self._debug_pending
        self._debug_pending = None
        if not self.debug_visible:
            return
        try:
            view = self.query_one(AgentDebugView)
        except NoMatches:
            return
        try:
            report = self.debug_report(pending)
        except Exception:  # noqa: BLE001 - ni el diagnóstico tumba la app
            return
        view.update_report(report)

    # --- comandos --------------------------------------------------------

    async def show_debug_text(self) -> str:
        """Texto plano del reporte, para `/debug --text` y para copiar."""
        from src.engine.context.meter.debug_render import render_debug_report

        return render_debug_report(self.debug_report())
