"""Panel lateral: skills cargadas y estado de los servidores MCP."""

from __future__ import annotations

from textual import work
from textual.app import ScreenStackError
from textual.css.query import NoMatches

from src.ui.widgets.sidebar_panel import MCPServerState, SidebarPanel


class SidebarMixin:
    """Mantiene el panel lateral al día.

    Espera que la app defina ``_loaded_skills`` y ``_mcp_manager``.
    """

    _loaded_skills: object | None
    _mcp_manager: object | None

    def refresh_sidebar(self) -> None:
        """Skills cargadas en la sesión, arriba del roadmap."""
        try:
            panel = self.query_one(SidebarPanel)
        except NoMatches, ScreenStackError:
            return
        names = self._loaded_skills.names() if self._loaded_skills is not None else ()
        panel.update_skills(names)

    @work(exclusive=True)
    async def refresh_mcp_status(self) -> None:
        """Conteo real de servidores MCP: conecta y actualiza el panel.

        Corre en un worker para no bloquear el arranque: levantar los servidores
        puede tardar. Si falla, el panel se queda con lo que ya sabía.
        """
        if self._mcp_manager is None or not self._mcp_manager:
            return
        try:
            statuses = await self._mcp_manager.describe(connect=True)
        except Exception:  # noqa: BLE001 - el diagnóstico nunca rompe la app
            return
        self.apply_mcp_status(statuses)

    def apply_mcp_status(self, statuses: list[object]) -> None:
        """Vuelca el estado de MCP al panel lateral."""
        servers = [MCPServerState(name=status.name, ok=not status.error) for status in statuses if status.enabled]
        try:
            self.query_one(SidebarPanel).update_mcp(servers)
        except NoMatches, ScreenStackError:
            return
