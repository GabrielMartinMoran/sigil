"""Side panel diagnostics: the model in use, the context budget and the MCP servers.

The column has two boxes. This one is the top: what the agent is doing with, and
how much room it has left. Under it, ``RoadmapPanel`` carries the plan. The split
is not cosmetic — this box is as tall as its content up to a share of the column,
and the plan takes whatever is left and scrolls inside it, so neither one can push
the composer off the screen.

Both boxes scroll on their own, which is why both are containers with a body inside
and not a bare `Static`: a `Static` reports its own size as its virtual size, so
overflow is clipped silently instead of becoming a scrollbar, and a clipped list of
MCP servers looks exactly like a short one.

The context budget is a line of the model section and not a section of its own: it
is a property of that model's window, it is read with the model, and a heading for
one number cost two rows in a 34-column panel. It reads as ``context: 66% (132K /
200K)``, the same shape as ``effort: high`` above it.

A section with nothing to say does not appear at all, so the panel stays short on a
cold start and grows as the session does.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from src.ui.palette import color
from src.ui.token_format import compact_tokens

OK_MARK = '●'
FAIL_MARK = '✗'

WARNING_PERCENT = 70.0
CRITICAL_PERCENT = 90.0


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


class SidebarPanel(Vertical):
    """Model (with its context budget), loaded skills and MCP state, in one scrolling box."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._skills: tuple[str, ...] = ()
        self._servers: tuple[MCPServerState, ...] = ()
        self._mcp_known = False
        self._model: ModelState | None = None
        self._context_state: ContextState | None = None

    def compose(self) -> ComposeResult:
        yield Static(Text(''), id='sidebar-body')

    # --- state --------------------------------------------------------------

    def update_model(self, state: ModelState | None) -> None:
        self._model = state
        self._repaint()

    def update_context(self, state: ContextState | None) -> None:
        """Context usage. It is hidden until a model is configured: the usage of
        a window that does not exist yet would be a number that means nothing."""
        self._context_state = state
        self._repaint()

    def update_skills(self, names: Sequence[str]) -> None:
        self._skills = tuple(names)
        self._repaint()

    def update_mcp(self, servers: Sequence[MCPServerState]) -> None:
        """MCP server state. With no servers, the section does not appear."""
        self._servers = tuple(servers)
        self._mcp_known = True
        self._repaint()

    def refresh_palette(self) -> None:
        """Rebuilds the text after a theme change.

        The panel's content is a Rich `Text` built once per state change, and the
        widget caches it. Without this the section headings keep the colors of the
        theme the app started with.
        """
        self._repaint()

    def _repaint(self) -> None:
        """Pushes the rebuilt text into the body.

        The body may not exist yet: the panel is updated from `on_mount` of the app
        and from a worker, and both can land before `compose` has run.
        """
        try:
            self.query_one('#sidebar-body', Static).update(self._build())
        except Exception:  # noqa: BLE001 - not mounted yet; `on_mount` paints it
            return

    # --- rendering ----------------------------------------------------------

    def _build(self) -> Text:
        text = Text()
        self._append_model(text)
        self._append_skills(text)
        self._append_mcp(text)
        return text

    @property
    def text(self) -> str:
        """The panel as plain text, for tests and diagnostics."""
        return self._build().plain

    def _section(self, text: Text, title: str, *, first: bool = False) -> None:
        """A section heading. The blank line belongs to the heading, not the content."""
        if not first and text.plain:
            text.append('\n')
        text.append(f'{title}\n', style=f'bold {color(self, "sidebar-section")}')

    def _append_model(self, text: Text) -> None:
        """The model, its reasoning effort and the budget of its window.

        The context line lives here rather than in a section of its own because it
        is the same fact as the model: which window, and how much of it is gone.
        """
        self._section(text, 'Model', first=True)
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
        self._append_context_line(text)

    def _append_context_line(self, text: Text) -> None:
        state = self._context_state
        if state is None:
            return
        text.append(' context: ', style='dim')
        text.append(_percent_text(state.percent), style=_percent_style(state.percent))
        if state.percent is not None and state.window:
            # The pair rides with the percentage: without one there is nothing to
            # compare the used tokens against, and `? (? / 200K)` reads like a bug.
            text.append(f' ({compact_tokens(state.used)} / {compact_tokens(state.window)})', style='dim')
        text.append('\n')

    def _append_skills(self, text: Text) -> None:
        self._section(text, 'Loaded skills')
        if not self._skills:
            text.append(' none yet\n', style='dim')
            return
        for name in self._skills:
            text.append(' • ', style='green')
            text.append(f'{name}\n')

    def _append_mcp(self, text: Text) -> None:
        if not (self._mcp_known and self._servers):
            return
        ok = sum(1 for server in self._servers if server.ok)
        failed = len(self._servers) - ok
        if text.plain:
            text.append('\n')
        text.append('MCP ', style=f'bold {color(self, "sidebar-section")}')
        text.append(f'({ok}', style='green')
        if failed:
            text.append(', ', style='dim')
            text.append(f'{failed} {FAIL_MARK}', style='bold red')
        text.append(')\n', style='green')
        for server in self._servers:
            if server.ok:
                text.append(f' {OK_MARK} ', style='green')
                text.append(f'{server.name}\n')
            else:
                text.append(f' {FAIL_MARK} ', style='bold red')
                text.append(f'{server.name}\n', style='red')


def _percent_text(percent: float | None) -> str:
    """Whole percentage. A decimal would cost three columns to say nothing."""
    return '?' if percent is None else f'{percent:.0f}%'


def _percent_style(percent: float | None) -> str:
    """The same escalation the room has always used: green, yellow, red."""
    if percent is None:
        return 'dim'
    if percent < WARNING_PERCENT:
        return 'green'
    if percent < CRITICAL_PERCENT:
        return 'yellow'
    return 'bold red'
