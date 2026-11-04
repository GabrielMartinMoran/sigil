"""Top sidebar panel: active model, context usage, loaded skills and MCP state.

It is the quick diagnostic of "what is the agent doing and with what?": which
account and model it is using, how full the window is, which skills it loaded in
the session, and which MCP servers are up.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.widgets import Static

from src.ui.token_format import compact_tokens

_OK_MARK = '●'
_FAIL_MARK = '✗'

_WARNING_PERCENT = 70.0
_CRITICAL_PERCENT = 90.0


@dataclass(frozen=True, slots=True)
class MCPServerState:
    """State of an MCP server for the panel."""

    name: str
    ok: bool


@dataclass(frozen=True, slots=True)
class ModelState:
    """What the panel shows about the active model."""

    alias: str = ''
    provider: str = ''
    auth: str = ''
    model: str = ''
    effort: str = 'default'


@dataclass(frozen=True, slots=True)
class ContextState:
    """How much of the window the next request is going to take.

    ``used`` is the **projected** number, not the measured one: the panel answers
    "does the next request fit?", and the measured value describes a request that
    already happened.
    """

    percent: float | None = None
    used: int | None = None
    window: int | None = None


class SidebarPanel(Static):
    """Model, context usage, skills loaded in the session and MCP server state."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._skills: tuple[str, ...] = ()
        self._servers: tuple[MCPServerState, ...] = ()
        self._mcp_known = False
        self._model: ModelState | None = None
        self._context_state: ContextState | None = None

    def update_model(self, state: ModelState | None) -> None:
        self._model = state
        self.update(self._build())

    def update_context(self, state: ContextState | None) -> None:
        """Context usage. It is hidden until a model is configured: the usage of
        a window that does not exist yet would be a number that means nothing."""
        self._context_state = state
        self.update(self._build())

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
        self._append_model(text)
        self._append_context(text)
        self._append_skills(text)
        self._append_mcp(text)
        return text

    @property
    def text(self) -> str:
        """The panel as plain text, for tests and diagnostics."""
        return self._build().plain

    def _append_model(self, text: Text) -> None:
        text.append('Model\n', style='bold')
        state = self._model
        if state is None or not state.alias:
            text.append(' not configured — /provider\n', style='dim')
            return
        text.append(' ', style='dim')
        text.append(state.alias, style='cyan')
        text.append(f' · {state.provider}\n', style='dim')
        text.append(' ', style='dim')
        text.append(state.model, style='bold')
        text.append('\n')
        text.append(' effort: ', style='dim')
        text.append(f'{state.effort}\n', style='magenta')

    def _append_context(self, text: Text) -> None:
        state = self._context_state
        if state is None or self._model is None or not self._model.alias:
            return
        text.append('\nContext\n', style='bold')
        text.append(' ', style='dim')
        text.append(_percent_text(state.percent), style=_percent_style(state.percent))
        if state.percent is not None and state.window:
            # The pair rides with the percentage: without one there is nothing to
            # compare the used tokens against, and `? (? / 200K)` reads like a bug.
            text.append(f' ({compact_tokens(state.used)} / {compact_tokens(state.window)})', style='dim')
        text.append('\n')

    def _append_skills(self, text: Text) -> None:
        text.append('\nLoaded skills\n', style='bold')
        if not self._skills:
            text.append(' none yet\n', style='dim')
            return
        for name in self._skills:
            text.append(' • ', style='green')
            text.append(f'{name}\n')

    def _append_mcp(self, text: Text) -> None:
        ok = sum(1 for server in self._servers if server.ok)
        failed = len(self._servers) - ok
        if not (self._mcp_known and self._servers):
            return
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


def _percent_text(percent: float | None) -> str:
    """Whole percentage. A decimal would cost three columns to say nothing."""
    return '?' if percent is None else f'{percent:.0f}%'


def _percent_style(percent: float | None) -> str:
    """The same escalation the room has always used: green, yellow, red."""
    if percent is None:
        return 'dim'
    if percent < _WARNING_PERCENT:
        return 'green'
    if percent < _CRITICAL_PERCENT:
        return 'yellow'
    return 'bold red'
