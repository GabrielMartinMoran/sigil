"""Side panel: active model, context usage, roadmap, loaded skills and MCP state."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage
from textual import work
from textual.app import ScreenStackError
from textual.css.query import NoMatches

from src.engine.context.meter.context_meter import ContextMeter
from src.engine.skills.loaded import LoadedSkills
from src.ui.widgets.sidebar_panel import ContextState, MCPServerState, SidebarPanel


class SidebarMixin:
    """Keeps the side panel up to date.

    It expects the app to define ``_loaded_skills``, ``_mcp_manager``, ``_meter``
    and ``_history``.
    """

    _loaded_skills: LoadedSkills | None
    _mcp_manager: object | None
    _meter: ContextMeter
    _history: list[ModelMessage]

    def refresh_sidebar(self) -> None:
        """Model, workspace, context, roadmap, skills and MCP, in one column."""
        try:
            panel = self.query_one(SidebarPanel)
        except NoMatches, ScreenStackError:
            return
        names = self._loaded_skills.names() if self._loaded_skills is not None else ()
        state = getattr(self, 'model_state', None)
        panel.update_model(state() if callable(state) else None)
        panel.update_context(self.context_state())
        panel.update_skills(names)

    def refresh_context(self, messages: Sequence[ModelMessage] | None = None) -> None:
        """Repaints only the context section.

        It is the hot path: a turn calls it on every checkpoint, and the model and
        the skills did not move. During a turn it is given the run history, which
        runs ahead of `self._history`.
        """
        try:
            panel = self.query_one(SidebarPanel)
        except NoMatches, ScreenStackError:
            return
        panel.update_context(self.context_state(messages))

    def context_state(self, messages: Sequence[ModelMessage] | None = None) -> ContextState:
        """How full the window is, from the meter.

        The **projected** number, not the measured one: the panel answers whether
        the next request fits, and the measured value describes the previous one.

        With no history there is nothing measured and nothing estimated, so the
        real usage is zero. Reporting it as unknown would paint `?` on every cold
        start, which is the moment the panel is read most.
        """
        snapshot = self._meter.snapshot(self._history if messages is None else messages)
        if snapshot.projected is None and snapshot.window:
            return ContextState(percent=0.0, used=0, window=snapshot.window)
        return ContextState(
            percent=snapshot.projected_percent,
            used=snapshot.projected,
            window=snapshot.window,
        )

    @work(exclusive=True)
    async def refresh_mcp_status(self) -> None:
        """Real MCP server count: it connects and updates the panel.

        It runs in a worker so as not to block startup: bringing the servers
        up can take time. If it fails, the panel keeps what it already knew.
        """
        if self._mcp_manager is None or not self._mcp_manager:
            return
        try:
            statuses = await self._mcp_manager.describe(connect=True)
        except Exception:  # noqa: BLE001 - diagnostics never break the app
            return
        self.apply_mcp_status(statuses)

    def apply_mcp_status(self, statuses: list[object]) -> None:
        """Dumps the MCP state into the side panel."""
        servers = [MCPServerState(name=status.name, ok=not status.error) for status in statuses if status.enabled]
        try:
            self.query_one(SidebarPanel).update_mcp(servers)
        except NoMatches, ScreenStackError:
            return
