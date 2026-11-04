"""Side panel: loaded skills and MCP server state."""

from __future__ import annotations

from textual import work
from textual.app import ScreenStackError
from textual.css.query import NoMatches

from src.ui.widgets.sidebar_panel import MCPServerState, SidebarPanel


class SidebarMixin:
    """Keeps the side panel up to date.

    It expects the app to define ``_loaded_skills`` and ``_mcp_manager``.
    """

    _loaded_skills: object | None
    _mcp_manager: object | None

    def refresh_sidebar(self) -> None:
        """Skills loaded in the session, above the roadmap."""
        try:
            panel = self.query_one(SidebarPanel)
        except NoMatches, ScreenStackError:
            return
        names = self._loaded_skills.names() if self._loaded_skills is not None else ()
        panel.update_skills(names)

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
