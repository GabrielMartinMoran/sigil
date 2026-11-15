"""The session's tree of agents, as a list you can walk, filter and manage.

`ctrl+t` opens it. It answers one question at a glance — *what is running under this
conversation, and how is each one doing?* — and it is the only place where a
subagent is reachable at all, because the picker (`ctrl+a`) lists agents only.

**Attention first, history second, at every level.** The root heads the list; then the
branches that still hold work, in tree order; then the branches that are finished,
newest first, under a rule that says where history begins. A branch is a subtree and
it moves as one, so a developer and the QAs it summoned stay together whether they are
live or history — and the same rule repeats inside the branch, which is what keeps a
running QA from ending up at the bottom of a long one. Forty finished siblings
therefore sit below the two that are working, instead of burying them: the list is
read from the top, and the top is what is happening.

**The gutter is the tree.** Two columns per level. A flat tree — every node one level
down, which is what a single turn of a squad produces — keeps plain indentation. As
soon as a subagent summons another, the indent becomes the ancestry: `│` for an
ancestor whose branch goes on below, `├` and `└` for the row's own. That is what makes
a row traceable to its parent when the parent is twenty rows up, and it costs the
columns the indentation was already spending.

**`/` filters.** Every other key here acts on a node (`b`, `k`), so typing cannot be
the default without taking those away; `/` hands the keyboard to the filter, which
matches the label, the profile, the id, the state and the task. `Esc` gives the keys
back to the list, and `Esc` again closes. The filter is a search over the same rows,
not a second list: the tree, the branch order and the rules stay in place.

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
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.timer import Timer
from textual.widgets import Input, OptionList, Static

from src.core.agents.tree.models.agent_node import AgentNode
from src.core.agents.tree.models.node_status import NodeStatus
from src.ui.widgets.agent_tree_rows import arrange, empty_row, guides, history_row, matches, node_row, status_text
from src.ui.widgets.gap import Gap
from src.ui.widgets.provider_modal import ProviderModal
from src.ui.widgets.status_bar import SPINNER_FRAMES, SPINNER_INTERVAL
from src.ui.widgets.tree_action import TreeAction
from src.ui.widgets.tree_action_kind import TreeActionKind

"""Same marks as the header of the node view: one vocabulary for one thing."""

"""Where the finished branches start, and what they are called."""

FILTER_ID = 'tree-filter'
LIST_ID = 'tree-list'
STATUS_ID = 'tree-status'

"""What the user asked for on a row: look at it, background it, or stop it."""


class AgentTreeScreen(ProviderModal):
    """Returns what to do with the chosen node, or ``None``."""

    TITLE_TEXT = 'Agents'
    HINT_TEXT = '↑/↓ move · Enter open · b background · k stop · / filter · Esc close'

    # Priority on the screen: the focus is the list, but the arrows would still be
    # taken by it; declaring them here is what makes the paging keys work the same
    # way as in the other pickers. `b`, `k` and `/` are deliberately not priority:
    # when the filter has the keyboard, those keys are letters in a search.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Close', show=False, priority=True),
        Binding('up', 'move(-1)', 'Up', show=False, priority=True),
        Binding('down', 'move(1)', 'Down', show=False, priority=True),
        Binding('pageup', 'move(-5)', 'Page up', show=False, priority=True),
        Binding('pagedown', 'move(5)', 'Page down', show=False, priority=True),
        Binding('enter', 'open', 'Open', show=False, priority=True),
        Binding('b', 'detach', 'Background', show=False),
        Binding('k', 'kill', 'Stop', show=False),
        Binding('/', 'filter', 'Filter', show=False),
    ]

    def __init__(self, nodes: Sequence[AgentNode], *, viewed: str = '') -> None:
        super().__init__()
        # Not called `_nodes`: that is Textual's own list of child widgets, and a
        # node put in it takes the screen down when it is pushed.
        self._tree_nodes = list(nodes)
        # The painted rows: a node, or a gap — the rule between work and history, or
        # the empty state when the session agent has summoned nobody.
        self._rows: list[AgentNode | Gap] = []
        self._guides: list[str] = []
        self._viewed = viewed
        self._frame = 0
        self._timer: Timer | None = None

    def compose_body(self) -> ComposeResult:
        yield Static('', id=STATUS_ID)
        yield Input(placeholder='Filter by name, id, state or task…', id=FILTER_ID)
        yield OptionList(id=LIST_ID)

    def on_mount(self) -> None:
        self.query_one(OptionList).focus()
        self._paint()

    # --- painting ----------------------------------------------------------

    def set_nodes(self, nodes: Sequence[AgentNode]) -> None:
        """Repaints with a newer state of the tree, keeping the highlighted row."""
        self._tree_nodes = list(nodes)
        self._paint()

    def _paint(self) -> None:
        options = self.query_one(OptionList)
        previous = options.highlighted_option
        keep = None if previous is None else previous.id

        visible = matches(self._tree_nodes, self.query_one(f'#{FILTER_ID}', Input).value)
        painted, separator = arrange(visible)
        strips = guides(painted)
        self._rows = []
        self._guides = []
        for index, node in enumerate(painted):
            if index == separator:
                # The rule goes between the two sections, and it has no guide.
                self._rows.append(Gap.HISTORY)
                self._guides.append('')
            self._rows.append(node)
            self._guides.append(strips[index])
        if not any(not node.is_root for node in self._tree_nodes):
            # Nothing summoned yet: the row is where the tree will be, and it says so.
            self._rows.append(Gap.EMPTY)
            self._guides.append('')

        options.clear_options()
        for index, row in enumerate(self._rows):
            if row is Gap.HISTORY:
                options.add_option(history_row(len(painted) - index))
            elif row is Gap.EMPTY:
                options.add_option(empty_row())
            else:
                options.add_option(node_row(row, viewed=self._viewed, guide=self._guides[index], frame=self._spinner()))
        self._update_status(sum(1 for node in painted if not node.is_root))
        options.highlighted = self._index(None if keep is None else str(keep))
        self._sync_timer()

    def _update_status(self, shown: int) -> None:
        query = self.query_one(f'#{FILTER_ID}', Input).value
        self.query_one(f'#{STATUS_ID}', Static).update(status_text(self._tree_nodes, shown=shown, query=query))

    def _index(self, node_id: str | None) -> int:
        """Where the cursor goes: the row it was on, else the first job that runs.

        The root is skipped on purpose: it is the trunk and it reports `running` for
        as long as the session is open, so it is never the row a reader came for.
        """
        wanted = node_id or self._viewed
        for index, row in enumerate(self._rows):
            if isinstance(row, AgentNode) and row.id == wanted:
                return index
        for index, row in enumerate(self._rows):
            if isinstance(row, AgentNode) and not row.is_root and not row.status.finished:
                return index
        return 0

    # --- the spinner -------------------------------------------------------

    def _spinner(self) -> str:
        return SPINNER_FRAMES[self._frame]

    def _sync_timer(self) -> None:
        """Keeps a timer only while something is actually spinning on screen."""
        running = any(
            isinstance(row, AgentNode) and row.status is NodeStatus.RUNNING and not row.is_root for row in self._rows
        )
        if running and self._timer is None:
            self._timer = self.set_interval(SPINNER_INTERVAL, self._advance)
        elif not running and self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _advance(self) -> None:
        self._frame = (self._frame + 1) % len(SPINNER_FRAMES)
        options = self.query_one(OptionList)
        redrawn = False
        for index, row in enumerate(self._rows):
            if not isinstance(row, AgentNode) or row.status is not NodeStatus.RUNNING or row.is_root:
                continue
            options.replace_option_prompt(
                row.id, node_row(row, viewed=self._viewed, guide=self._guides[index], frame=self._spinner()).prompt
            )
            redrawn = True
        if redrawn:
            options.refresh()

    # --- actions -----------------------------------------------------------

    def action_move(self, delta: int) -> None:
        options = self.query_one(OptionList)
        count = options.option_count
        if not count:
            return
        current = options.highlighted
        index = -1 if current is None else current
        step = 1 if delta > 0 else -1
        for _ in range(abs(delta)):
            candidate = index + step
            while 0 <= candidate < count and options.get_option_at_index(candidate).disabled:
                candidate += step
            if not 0 <= candidate < count:
                break
            index = candidate
        if index >= 0:
            options.highlighted = index

    def action_open(self) -> None:
        self._decide('open')

    def action_detach(self) -> None:
        """`b`: the node stops blocking whoever waits for it and answers later."""
        self._decide('detach')

    def action_kill(self) -> None:
        """`k`: the node is stopped, along with everything it summoned."""
        self._decide('kill')

    def action_filter(self) -> None:
        """`/`: the keys become a search instead of actions on a node."""
        self.query_one(f'#{FILTER_ID}', Input).focus()

    def _decide(self, kind: TreeActionKind) -> None:
        option = self.query_one(OptionList).highlighted_option
        if option is None or option.id is None:
            return
        self.dismiss(TreeAction(kind=kind, node_id=str(option.id)))

    def action_cancel(self) -> None:
        """`Esc` steps back one level: the filter, then the list, then the chat."""
        field = self.query_one(f'#{FILTER_ID}', Input)
        if self.focused is field:
            field.value = ''
            self.query_one(OptionList).focus()
            return
        if field.value:
            field.value = ''
            return
        self.dismiss(None)

    # --- the filter --------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != FILTER_ID:
            return
        self._paint()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # The screen-level `enter` binding already opens the highlighted row; this
        # keeps the Input from doing an Enter of its own.
        if event.input.id == FILTER_ID:
            self.action_open()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(TreeAction(kind='open', node_id=str(event.option.id)))


__all__ = [
    'AgentTreeScreen',
    'TreeAction',
    'arrange',
    'guides',
    'history_row',
    'matches',
    'node_row',
    'status_text',
]
