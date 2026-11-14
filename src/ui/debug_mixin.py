"""Debug rendering: the service measures; the attached terminal only paints."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import ModelMessage
from textual import work
from textual.css.query import NoMatches

from src.engine.context.meter.debug import DebugReport, build_debug_report
from src.engine.context.meter.debug_render import render_debug_report
from src.engine.context.meter.debug_wire import DebugSnapshot
from src.engine.context.meter.request import RequestSnapshot
from src.ui.service_client import ServiceClientError
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.debug_view import AgentDebugView
from src.ui.widgets.prompt_input import PromptInput

_EMPTY_REQUEST = RequestSnapshot()
_REFRESH_DELAY = 0.15
SERVICE_DEBUG = 'debug-service'


class DebugMixin:
    """Toggles between the chat and the agent debug view.

    In process it expects the app to define ``_history``, ``_meter`` and
    ``_context_capability``; attached, ``_client`` and ``_service_state``, and it
    reads the service instead of measuring a run this process does not have.
    """

    _history: list[ModelMessage]
    _meter: Any
    _context_capability: Any
    _client: Any
    _service_state: dict[str, Any] | None
    debug_visible: bool
    _debug_timer: Any
    _debug_pending: list[ModelMessage] | None

    # --- toggle ----------------------------------------------------------

    def toggle_debug(self) -> None:
        """Shows or hides the debug view, replacing the chat."""
        self.set_debug_visible(not self.debug_visible)

    def set_debug_visible(self, visible: bool) -> None:
        """Sets the debug view state and redraws."""
        self.debug_visible = visible
        try:
            view = self.query_one(AgentDebugView)
            chat = self.query_one(ChatLog)
            prompt = self.query_one(PromptInput)
        except NoMatches:
            return
        view.display = visible
        # It replaces, it does not coexist: the space is the same and the view is the same
        # conversation told from the other side. The input stays active and
        # focused, on purpose: the view refreshes on every step of the turn, so
        # you can send a prompt and watch live what context and cache it costs.
        chat.display = not visible
        if visible:
            self.refresh_debug(immediate=True)
        else:
            self._cancel_debug_refresh()
        prompt.focus()

    # --- reporte ---------------------------------------------------------

    def debug_from_service(self) -> bool:
        """Whether the report comes from the service instead of from this process."""
        return getattr(self, '_client', None) is not None

    async def read_service_debug_report(self) -> DebugSnapshot:
        """Read the server's full report; never measure local empty history."""
        return await self._client.debug()

    @work(exclusive=True, group=SERVICE_DEBUG)
    async def refresh_service_debug(self) -> None:
        """Fetch once per coalesced burst and reject responses for another session."""
        if not self.debug_visible:
            return
        try:
            snapshot = await self.read_service_debug_report()
        except ServiceClientError:
            self._debug_unavailable()
            return
        state = self._service_state or {}
        session_id = (state.get('session') or {}).get('id')
        if not self.debug_visible or (session_id and snapshot.session_id != session_id):
            return
        try:
            view = self.query_one(AgentDebugView)
        except NoMatches:
            return
        if view.last_report != snapshot.report:
            view.update_report(snapshot.report)

    def _debug_unavailable(self) -> None:
        """A failed read is not a zero measurement or a silently current old report."""
        if not self.debug_visible:
            return
        try:
            self.query_one(AgentDebugView).show_unavailable(
                'Debug unavailable — could not read /api/debug. Toggle to retry.'
            )
        except NoMatches:
            pass

    def debug_report(self, messages: Sequence[ModelMessage] | None = None) -> DebugReport:
        """Builds the debug report with the current log and the next request projection."""
        history = list(self._history if messages is None else messages)
        capability = self._context_capability
        request: RequestSnapshot | None = None
        view_result = None
        if capability is not None:
            request = capability.last_request
            try:
                # The next request's view, not the previous one's: what the
                # agent is going to receive with the current history.
                view_result = capability.project(history)
            except Exception:  # noqa: BLE001 - diagnostics never break the UI
                view_result = capability.last_view
        return build_debug_report(
            history,
            snapshot=self._meter.snapshot(history),
            request=request or _EMPTY_REQUEST,
            view_result=view_result,
            effort=self.effort_in_effect(),
            model=self._model_snapshot().label,
        )

    def refresh_debug(self, messages: Sequence[ModelMessage] | None = None, *, immediate: bool = False) -> None:
        """Requests a repaint of the debug view. It does nothing if it is closed.

        Without ``immediate`` the repaint is coalesced: a single one is scheduled per
        window and the latest history wins. Attached there is no history to hand
        over — the read of the service's meter is the repaint — so the pending
        history is cleared and the worker does the rest.
        """
        if not self.debug_visible:
            return
        if self.debug_from_service():
            self._debug_pending = None
        else:
            run = getattr(self, '_current_run', None)
            current = run.all_messages() if messages is None and run is not None else self._history
            self._debug_pending = list(current if messages is None else messages)
        if immediate:
            self._flush_debug()
        elif self._debug_timer is None:
            self._debug_timer = self.set_timer(_REFRESH_DELAY, self._flush_debug)

    def _cancel_debug_refresh(self) -> None:
        """Discards a scheduled repaint: the view is no longer shown."""
        if self._debug_timer is not None:
            self._debug_timer.stop()
            self._debug_timer = None
        self._debug_pending = None
        if self.debug_from_service():
            self.workers.cancel_group(self, SERVICE_DEBUG)

    def _flush_debug(self) -> None:
        """Paints the pending report. It is the coalescing timer callback."""
        self._debug_timer = None
        pending = self._debug_pending
        self._debug_pending = None
        if not self.debug_visible:
            return
        if self.debug_from_service():
            self.refresh_service_debug()
            return
        try:
            view = self.query_one(AgentDebugView)
        except NoMatches:
            return
        try:
            report = self.debug_report(pending)
        except Exception:  # noqa: BLE001 - not even diagnostics bring the app down
            return
        view.update_report(report)

    # --- commands ---------------------------------------------------------

    async def show_debug_text(self) -> str:
        """Plain-text report, for `/debug --text` and for copying."""
        if self.debug_from_service():
            try:
                snapshot = await self.read_service_debug_report()
            except ServiceClientError:
                return 'Debug unavailable — could not read /api/debug.'
            text = render_debug_report(snapshot.report)
            return f'{text}\n\n{snapshot.timing}' if snapshot.timing else text

        report = render_debug_report(self.debug_report())
        timings = getattr(self, '_turn_timings', None)
        return report if timings is None else f'{report}\n\n{timings.render()}'
