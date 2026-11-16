"""Helpers that used to live in agent_tree_screen."""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text
from textual.widgets.option_list import Option

from src.core.agents.tree.models.agent_node import AgentNode
from src.core.agents.tree.models.node_status import NodeStatus
from src.ui.widgets.panels.agent_view_text import elapsed_text

MARK = {
    NodeStatus.RUNNING: ('●', 'bold yellow'),
    NodeStatus.DONE: ('✓', 'green'),
    NodeStatus.FAILED: ('✖', 'bold red'),
    NodeStatus.CANCELLED: ('◼', 'dim'),
}


INDENT = '  '


BAR = '│ '


BRANCH = '├ '


LAST = '└ '


HISTORY_LABEL = 'finished'


def _count(number: int, noun: str) -> str:
    """`1 subagent`, `3 subagents`: the line is read, not parsed."""
    return f'{number} {noun}' if number == 1 else f'{number} {noun}s'


def state_of(node: AgentNode) -> str:
    """The word for what the node is doing, which is not always its status.

    A node that used up its steps is ``done`` — it answered — but the reason matters
    to whoever reads the list: it can be resumed, and its report is partial.
    """
    if node.budget_exhausted and node.status is NodeStatus.DONE:
        return 'out of steps'
    return str(node.status)


def arrange(nodes: Sequence[AgentNode]) -> tuple[list[AgentNode], int]:
    """The painted order, and where the history section starts (``-1`` for none).

    The rule is the same at every level, not only at the top: **a subtree that still
    holds work comes before one that is finished**, and the finished ones are ordered
    by when they ended, newest first. That is what makes the list answer from the top
    down. A branch is a subtree — the root's children, and any node whose parent is
    not in the list, which is what filtering leaves behind.

    Inside a subtree the same rule repeats, so a running QA under a developer stays
    above the developer's finished ones instead of ending up at the bottom of a long
    branch. Siblings that both hold work keep tree order, which is the order they
    were summoned in: attention decides the sections, time decides inside them.
    """
    by_id = {node.id: node for node in nodes}
    children: dict[str, list[AgentNode]] = {}
    trunk: list[AgentNode] = []
    heads: list[AgentNode] = []
    for node in nodes:
        if node.is_root:
            trunk.append(node)
            continue
        parent = by_id.get(node.parent_id) if node.parent_id else None
        if parent is None:
            heads.append(node)
        else:
            children.setdefault(parent.id, []).append(node)

    live: dict[str, bool] = {}
    ends: dict[str, float] = {}

    def measure(node: AgentNode) -> None:
        kids = children.get(node.id, [])
        for kid in kids:
            measure(kid)
        live[node.id] = not node.status.finished or any(live[kid.id] for kid in kids)
        ends[node.id] = max([node.finished_at or node.started_at, *(ends[kid.id] for kid in kids)])

    def order(node: AgentNode) -> tuple[int, float]:
        """Work first in the order it started; then what ended, newest end first."""
        return (0, node.started_at) if live[node.id] else (1, -ends[node.id])

    painted: list[AgentNode] = list(trunk)
    branches = [*[kid for node in trunk for kid in children.get(node.id, [])], *heads]
    for branch in branches:
        measure(branch)
    working = [branch for branch in branches if live[branch.id]]
    finished = sorted((branch for branch in branches if not live[branch.id]), key=order)

    def emit(node: AgentNode) -> None:
        painted.append(node)
        for kid in sorted(children.get(node.id, []), key=order):
            emit(kid)

    for branch in working:
        emit(branch)
    separator = len(painted) if working and finished else -1
    for branch in finished:
        emit(branch)
    return painted, separator


def guides(nodes: Sequence[AgentNode]) -> list[str]:
    """The two-column gutter of each row, in the order the rows are painted.

    A flat list keeps plain indentation, because that is all a list of siblings
    needs. Once the tree has depth the gutter draws it, and a row whose parent is not
    on screen — filtered out — keeps its indentation and loses the bar, which is the
    truth about what the reader can see.
    """
    nested = any(node.depth > 1 for node in nodes)
    closes_at: dict[str | None, int] = {}
    for index, node in enumerate(nodes):
        closes_at[node.parent_id] = index

    out: list[str] = []
    open_branch: dict[int, bool] = {}
    for index, node in enumerate(nodes):
        closes = closes_at.get(node.parent_id) == index
        for level in [level for level in open_branch if level >= node.depth]:
            del open_branch[level]
        if nested and node.depth > 0:
            prefix = ''.join('  ' if open_branch.get(level, True) else BAR for level in range(1, node.depth))
            prefix += LAST if closes else BRANCH
        else:
            prefix = INDENT * node.depth
        out.append(prefix)
        open_branch[node.depth] = closes
    return out


def continuation(guide: str) -> str:
    """The gutter of the line under a row: the branch bar goes on, or it ends here."""
    if guide.endswith(BRANCH):
        return f'{guide[: -len(BRANCH)]}{BAR}'
    if guide.endswith(LAST):
        return f'{guide[: -len(LAST)]}{INDENT}'
    return guide


def node_row(node: AgentNode, *, viewed: str = '', guide: str | None = None, frame: str = '') -> Option:
    """One row: state, name, and the little that makes it identifiable.

    The profile name is not repeated: ``label`` *is* that name with its dashes
    opened up, so a row that painted both was saying the same thing twice. ``frame``
    is the spinner character the screen is on; a running node takes it, so the row
    moves while the work does.
    """
    mark, style = MARK.get(node.status, ('·', ''))
    # The root is the trunk and it is `running` for as long as the session is open,
    # so it does not spin: it would say the conversation is busy while it idles.
    if node.status is NodeStatus.RUNNING and frame and not node.is_root:
        mark = frame
    gutter = INDENT * node.depth if guide is None else guide
    line = Text()
    line.append(gutter, style='dim')
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
        # Under the name, on the line the branch bar continues, so the gutter reads
        # as one column of ancestry from the first row to the last.
        line.append(f'\n{continuation(gutter)}   {task[:110]}', style='dim')
    return Option(line, id=node.id)


def history_row(count: int) -> Option:
    """The rule between what is happening and what happened.

    Disabled, so the cursor cannot land on it and no action can be taken on it: it is
    a label, and the list is navigated by node.
    """
    text = Text()
    text.append(f'── {HISTORY_LABEL} · {count} ', style='dim')
    text.append('─' * 80, style='dim')
    return Option(text, id=None, disabled=True)


def empty_row() -> Option:
    """What the list says when the session agent has summoned nobody yet.

    The screen opens anyway: the state is information, and a key that answers with a
    toast instead of a window teaches the user that the window is broken. This row is
    where the tree will be, and it says when the tree gets here.
    """
    text = Text()
    text.append('   ', style='')
    text.append('Subagents appear here while the agent works.', style='dim italic')
    return Option(text, id=None, disabled=True)


def status_text(nodes: Sequence[AgentNode], *, shown: int | None = None, query: str = '') -> str:
    """The line above the list: how many there are, how many run, how many are left."""
    subagents = [node for node in nodes if not node.is_root]
    if not subagents:
        return '[dim]No subagents in this session yet.[/dim]'
    running = [node for node in subagents if not node.status.finished]
    background = [node for node in running if node.mode == 'async']
    finished = len(subagents) - len(running)
    parts = [_count(len(subagents), 'subagent'), f'{len(running)} running']
    if background:
        parts.append(f'{len(background)} in the background')
    if finished:
        parts.append(f'{finished} {HISTORY_LABEL}')
    if query and shown == 0:
        return f'[yellow]No agent matches “{query}”. Esc clears.[/yellow]'
    if query and shown is not None:
        return f'[dim]{" · ".join(parts)} — {shown} matching “{query}”[/dim]'
    return f'[dim]{" · ".join(parts)}[/dim]'


def matches(nodes: Sequence[AgentNode], query: str) -> list[AgentNode]:
    """The nodes a filter keeps: what it names, plus the root, which is the trunk.

    The root stays whether or not it matches: a list of matches with no top would
    read as a second tree, and the root is one row.
    """
    needle = ' '.join(query.split()).casefold()
    if not needle:
        return list(nodes)
    kept = []
    for node in nodes:
        haystack = f'{node.label} {node.name} {node.short_id} {state_of(node)} {node.mode} {node.task}'
        if node.is_root or needle in haystack.casefold():
            kept.append(node)
    return kept
