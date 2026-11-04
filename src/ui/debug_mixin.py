"""Agent debug view: `alt+d` replaces the chat with what the agent sees."""

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
"""Snapshot when no request has run in the session yet."""

_REFRESH_DELAY = 0.15
"""Coalescing seconds. During a turn the stream events arrive in
bursts and re-projecting the history on each one is wasted work: the body is
repainted once per window and no state is lost, because everything is derived
from the history."""


class DebugMixin:
    """Toggles between the chat and the agent debug view.

    It expects the app to define ``_history``, ``_meter``, ``_context_capability``
    and ``debug_visible``.
    """

    _history: list[ModelMessage]
    _meter: Any
    _context_capability: Any
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
            model=ConfigProvider.LLM_MODEL,
        )

    def refresh_debug(self, messages: Sequence[ModelMessage] | None = None, *, immediate: bool = False) -> None:
        """Requests a repaint of the debug view. It does nothing if it is closed.

        Without ``immediate`` the repaint is coalesced: a single one is scheduled per
        window and the latest history wins.
        """
        if not self.debug_visible:
            return
        self._debug_pending = list(self._history if messages is None else messages)
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

    def _flush_debug(self) -> None:
        """Paints the pending report. It is the coalescing timer callback."""
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
        except Exception:  # noqa: BLE001 - not even diagnostics bring the app down
            return
        view.update_report(report)

    # --- commands ---------------------------------------------------------

    async def show_debug_text(self) -> str:
        """Plain-text report, for `/debug --text` and for copying."""
        from src.engine.context.meter.debug_render import render_debug_report

        return render_debug_report(self.debug_report())
