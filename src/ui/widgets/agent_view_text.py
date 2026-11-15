"""Helpers that used to live in agent_view."""

from __future__ import annotations

from rich.text import Text

from src.core.agents.tree.models.agent_node import AgentNode
from src.core.agents.tree.models.node_status import NodeStatus

STATUS_STYLE = {
    NodeStatus.RUNNING: ('●', 'bold yellow', 'running'),
    NodeStatus.DONE: ('✓', 'green', 'done'),
    NodeStatus.FAILED: ('✖', 'bold red', 'failed'),
    NodeStatus.CANCELLED: ('◼', 'dim', 'cancelled'),
}


def elapsed_text(seconds: float) -> str:
    """Duration in the shortest unit that still says something."""
    if seconds < 60:
        return f'{seconds:.1f}s'
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f'{minutes}m{rest:02d}s'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h{minutes:02d}m'


def build_agent_header(node: AgentNode, *, parent_label: str, has_parent: bool) -> Text:
    """The fixed header: where this node is, and how it went.

    The task is **not** repeated here. It is the first message of the transcript
    right below, so a header that also carried it said the same thing twice and
    pushed the actual work down the screen — and the header stays fixed while the
    transcript scrolls, which made the duplication permanent.

    What the header knows and the transcript does not is the rest: who summoned it,
    how to leave, the state it ended in, and how long it took.
    """
    mark, style, word = STATUS_STYLE.get(node.status, ('·', '', str(node.status)))
    if node.budget_exhausted and node.status is NodeStatus.DONE:
        # It answered, so it is done — but the reason it stopped is what a reader
        # needs: the report is partial and the node can be resumed.
        mark, style, word = '◔', 'yellow', 'out of steps'
    header = Text()
    if has_parent:
        header.append('◀ ', style='dim')
        header.append(f'Esc returns to {parent_label}', style='dim')
        header.append('   ')
    header.append(node.label, style='bold')
    header.append(f'  #{node.short_id}', style='dim')
    header.append('  ')
    header.append(f'{mark} {word}', style=style)
    header.append(f' · {node.mode} · {elapsed_text(node.elapsed)}', style='dim')
    if node.runs > 1:
        header.append(f' · run {node.runs}', style='dim')
    if node.model:
        header.append(f' · {node.model}', style='dim')
    if node.channel.bidirectional:
        header.append(f' · {node.channel.describe()}', style='dim')
    if node.status is NodeStatus.FAILED and node.error:
        header.append('\n')
        header.append('Error: ', style='bold red')
        header.append(' '.join(node.error.split()), style='red')
    return header
