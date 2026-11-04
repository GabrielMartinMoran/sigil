"""Top sidebar panel: loaded skills and MCP state.

It is the quick diagnostic of "is the agent doing what it should?": which
skills it loaded in the session and which MCP servers are up.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.widgets import Static

_OK_MARK = '●'
_FAIL_MARK = '✗'


@dataclass(frozen=True, slots=True)
class MCPServerState:
    """State of an MCP server for the panel."""

    name: str
    ok: bool


class SidebarPanel(Static):
    """Skills loaded in the session and MCP server state."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._skills: tuple[str, ...] = ()
        self._servers: tuple[MCPServerState, ...] = ()
        self._mcp_known = False

    def update_skills(self, names: Sequence[str]) -> None:
        self._skills = tuple(names)
        self.update(self._build())

    def update_mcp(self, servers: Sequence[MCPServerState]) -> None:
        """MCP server state. With no servers, the section does not appear."""
        self._servers = tuple(servers)
        self._mcp_known = True
        self.update(self._build())

    def _build(self) -> Text:
        text = Text()
        text.append('Loaded skills\n', style='bold')
        if not self._skills:
            text.append(' none yet\n', style='dim')
        else:
            for name in self._skills:
                text.append(' • ', style='green')
                text.append(f'{name}\n')

        ok = sum(1 for server in self._servers if server.ok)
        failed = len(self._servers) - ok
        if self._mcp_known and self._servers:
            text.append('\nMCP ', style='bold')
            text.append(f'({ok}', style='green')
            if failed:
                text.append(', ', style='dim')
                text.append(f'{failed} {_FAIL_MARK}', style='bold red')
            text.append(')\n', style='green')
            for server in self._servers:
                if server.ok:
                    text.append(f' {_OK_MARK} ', style='green')
                    text.append(f'{server.name}\n')
                else:
                    text.append(f' {_FAIL_MARK} ', style='bold red')
                    text.append(f'{server.name}\n', style='red')

        return text
