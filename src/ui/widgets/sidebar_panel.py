"""Side panel: the model, the skills, the MCP servers and the roadmap, in one column.

The panel used to be two boxes: a diagnostics box on top and the roadmap under it
in a box of its own. They are read together —"what is it doing, with what, and how
far has it got?"— so the split bought nothing and cost a border, a margin and the
top half of a column that was mostly empty. It is one box now, and the roadmap is
the last section of the same list.

The sections are ordered by how often they are read: the model with the budget of
its window, the skills loaded in the session, the MCP servers and, at the end, the
plan of the task. A section with nothing to say does not appear at all, so the
panel stays short on a cold start and grows as the session does.

The context budget is a line of the model section and not a section of its own: it
is a property of that model's window, it is read with the model, and a heading for
one number cost two rows in a 34-column panel. It reads as ``context: 66% (132K /
200K)``, the same shape as ``effort: high`` above it.

The whole panel scrolls and not just the roadmap: with one box there is one
scrollbar, and giving the plan a nested one would put a scrollbar inside a
scrollbar in a column that is 34 characters wide.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from src.engine.roadmap.store import RoadmapStore
from src.ui.palette import color
from src.ui.token_format import compact_tokens

OK_MARK = '●'
FAIL_MARK = '✗'

WARNING_PERCENT = 70.0
CRITICAL_PERCENT = 90.0

ROADMAP_ICON = {'in_progress': '▶', 'pending': '○', 'blocked': '!', 'done': '✓'}
ROADMAP_STYLE = {
    'in_progress': 'bold blue',
    'pending': '',
    'blocked': 'bold yellow',
    'done': 'green strike',
}
ROADMAP_FALLBACK_ICON = '·'


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
    """Model, skills, MCP state and roadmap, in one scrolling column."""

    def __init__(self, store: RoadmapStore | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._store = store
        self._shown = True
        self._skills: tuple[str, ...] = ()
        self._servers: tuple[MCPServerState, ...] = ()
        self._mcp_known = False
        self._model: ModelState | None = None
        self._context_state: ContextState | None = None

    def compose(self) -> ComposeResult:
        # The body is inside a scroll and not the widget itself: a bare `Static`
        # reports its own size as its virtual size, so overflow would be clipped
        # silently instead of becoming a scrollbar, and a clipped list of MCP
        # servers looks exactly like a short one.
        with VerticalScroll(id='sidebar-scroll'):
            yield Static(Text(''), id='sidebar-body')

    def on_mount(self) -> None:
        self.refresh_from_store()

    # --- visibility ---------------------------------------------------------

    @property
    def shown(self) -> bool:
        """Whether the user asked to see the panel, regardless of its content.

        It is not called ``visible`` because Textual already uses that name for the
        CSS ``visibility`` rule, which reserves space instead of releasing it.
        """
        return self._shown

    def set_shown(self, shown: bool) -> None:
        """Shows or hides the panel and redraws it."""
        self._shown = shown
        self.refresh_from_store()

    # --- roadmap ------------------------------------------------------------

    def set_store(self, store: RoadmapStore | None) -> None:
        """Points the roadmap section at the session's plan."""
        self._store = store
        self.refresh_from_store()

    def refresh_from_store(self) -> None:
        """Repaints after the roadmap changed, or after a theme change.

        The user toggle wins: a changing roadmap does not bring back a panel the
        user hid.
        """
        self.display = self._shown
        if self._shown:
            self._repaint()

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
        widget caches it. Without this the section headings and the roadmap icons
        keep the colors of the theme the app started with.
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
        self._append_roadmap(text)
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

    def _append_roadmap(self, text: Text) -> None:
        """The plan of the task, last in the panel.

        It goes at the end on purpose: the model, the skills and the servers are
        what the agent is working with, and the plan is what it has done so far.
        With no store there is no plan to show, and the section stays away instead
        of announcing a feature this app was started without.
        """
        if self._store is None:
            return
        self._section(text, 'Roadmap')
        items = self._store.snapshot()
        if not items:
            text.append(' No tasks yet.\n', style='dim')
            return
        for item in items:
            style = ROADMAP_STYLE.get(item.status, '')
            icon = ROADMAP_ICON.get(item.status, ROADMAP_FALLBACK_ICON)
            text.append(f' {icon} ', style=style)
            text.append(item.title, style=style)
            text.append('\n')
            if item.notes:
                text.append(f'   {item.notes}\n', style='dim')


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
