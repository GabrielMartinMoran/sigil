"""The session's tree of agents, as a list you can walk and manage.

`ctrl+t` opens it. It answers one question at a glance — *what is running under this
conversation, and how is each one doing?* — and it is the only place where a
subagent is reachable at all, because the picker (`ctrl+a`) lists agents only.

It is a list and not a drawn tree: a branch is shown by indenting the row with the
depth of the node, and the vertical order is the tree order (depth first, siblings
by start time). Box-drawing characters would cost three columns of a 60-column box
and say the same thing as one level of indentation.

It is also where a subagent is *managed*, because it is the only surface that shows
one at all:

- `enter` opens the node: its task, its state and its transcript.
- `b` sends a running node to the background. That is the intuitive version of the
  tool's `mode="async"` on a call that is already blocking: the caller stops
  waiting, the node keeps working, and its answer arrives as a message.
- `k` stops it, and with it everything it summoned.

The ids the list shows are the ids the tools take, so what a user does here and
what a model does there are the same operations on the same objects.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import ClassVar, Literal

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.models.node_status import NodeStatus
from src.ui.widgets.agent_view import elapsed_text
from src.ui.widgets.provider_modal import ProviderModal

MARK = {
    NodeStatus.RUNNING: ('●', 'bold yellow'),
    NodeStatus.DONE: ('✓', 'green'),
    NodeStatus.FAILED: ('✖', 'bold red'),
    NodeStatus.CANCELLED: ('◼', 'dim'),
}
"""Same marks as the header of the node view: one vocabulary for one thing."""

INDENT = '  '

TreeActionKind = Literal['open', 'detach', 'kill']
"""What the user asked for on a row: look at it, background it, or stop it."""


@dataclass(frozen=True, slots=True)
class TreeAction:
    """A decision about one node, returned by the screen."""

    kind: TreeActionKind
    node_id: str


def state_of(node: AgentNode) -> str:
    """The word for what the node is doing, which is not always its status.

    A node that used up its steps is ``done`` — it answered — but the reason matters
    to whoever reads the list: it can be resumed, and its report is partial.
    """
    if node.budget_exhausted and node.status is NodeStatus.DONE:
        return 'out of steps'
    return str(node.status)


def node_row(node: AgentNode, *, viewed: str = '') -> Option:
    """One row: state, name, and the little that makes it identifiable.

    The profile name is not repeated: ``label`` *is* that name with its dashes
    opened up, so a row that painted both was saying the same thing twice.
    """
    mark, style = MARK.get(node.status, ('·', ''))
    line = Text()
    line.append(f'{INDENT * node.depth}', style='')
    line.append('●  ' if node.id == viewed else f'{mark}  ', style=style)
    line.append(node.label, style='bold')
    line.append(f'  #{node.short_id}', style='dim')
    line.append(f'  {state_of(node)}', style=style)
    line.append(f'  {elapsed_text(node.elapsed)}', style='dim')
    if node.mode != 'sync' and not node.is_root:
        line.append(f'  {node.mode}', style='dim')
    if node.runs > 1:
        line.append(f'  run {node.runs}', style='dim')
    if node.task and not node.is_root:
        task = ' '.join(node.task.split())
        line.append(f'\n{INDENT * node.depth}      {task[:110]}', style='dim')
    return Option(line, id=node.id)


def status_text(nodes: Sequence[AgentNode]) -> str:
    """The line above the list: how many there are, how many run, how many wait."""
    subagents = [node for node in nodes if not node.is_root]
    if not subagents:
        return '[dim]No subagents in this session yet.[/dim]'
    running = [node for node in subagents if not node.status.finished]
    background = [node for node in running if node.mode == 'async']
    parts = [f'{len(subagents)} subagents', f'{len(running)} running']
    if background:
        parts.append(f'{len(background)} in the background')
    return f'[dim]{" · ".join(parts)}[/dim]'


class AgentTreeScreen(ProviderModal):
    """Returns what to do with the chosen node, or ``None``."""

    TITLE_TEXT = 'Agents'
    HINT_TEXT = '↑/↓ move · Enter open · b background · k stop · Esc close'

    # Priority on the screen: the focus is the list, but the arrows would still be
    # taken by it; declaring them here is what makes the paging keys work the same
    # way as in the other pickers.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Close', show=False, priority=True),
        Binding('up', 'move(-1)', 'Up', show=False, priority=True),
        Binding('down', 'move(1)', 'Down', show=False, priority=True),
        Binding('pageup', 'move(-5)', 'Page up', show=False, priority=True),
        Binding('pagedown', 'move(5)', 'Page down', show=False, priority=True),
        Binding('enter', 'open', 'Open', show=False, priority=True),
        Binding('b', 'detach', 'Background', show=False, priority=True),
        Binding('k', 'kill', 'Stop', show=False, priority=True),
    ]

    def __init__(self, nodes: Sequence[AgentNode], *, viewed: str = '') -> None:
        super().__init__()
        # Not called `_nodes`: that is Textual's own list of child widgets, and a
        # node put in it takes the screen down when it is pushed.
        self._rows = list(nodes)
        self._viewed = viewed

    def compose_body(self) -> ComposeResult:
        yield Static('', id='tree-status')
        yield OptionList(id='tree-list')

    def on_mount(self) -> None:
        self._refresh()
        self.query_one(OptionList).focus()

    def set_nodes(self, nodes: Sequence[AgentNode]) -> None:
        """Repaints with a newer state of the tree, keeping the highlighted row."""
        self._rows = list(nodes)
        self._refresh()

    def _refresh(self) -> None:
        options = self.query_one(OptionList)
        previous = options.highlighted_option
        keep = None if previous is None else str(previous.id)
        options.clear_options()
        for node in self._rows:
            options.add_option(node_row(node, viewed=self._viewed))
        self.query_one('#tree-status', Static).update(status_text(self._rows))
        if self._rows:
            options.highlighted = self._index(keep)

    def _index(self, node_id: str | None) -> int:
        for index, node in enumerate(self._rows):
            if node.id == (node_id or self._viewed):
                return index
        return 0

    # --- actions -----------------------------------------------------------

    def action_move(self, delta: int) -> None:
        options = self.query_one(OptionList)
        count = options.option_count
        if not count:
            return
        current = options.highlighted or 0
        options.highlighted = min(max(current + delta, 0), count - 1)

    def action_open(self) -> None:
        self._decide('open')

    def action_detach(self) -> None:
        """`b`: the node stops blocking whoever waits for it and answers later."""
        self._decide('detach')

    def action_kill(self) -> None:
        """`k`: the node is stopped, along with everything it summoned."""
        self._decide('kill')

    def _decide(self, kind: TreeActionKind) -> None:
        option = self.query_one(OptionList).highlighted_option
        if option is None or option.id is None:
            return
        self.dismiss(TreeAction(kind=kind, node_id=str(option.id)))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(TreeAction(kind='open', node_id=str(event.option.id)))


__all__ = ['AgentTreeScreen', 'TreeAction', 'node_row', 'state_of', 'status_text']
