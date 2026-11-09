"""A subagent, opened: its task, its state and the conversation it had.

Navigating into a subagent is reading, not talking. The subagent has no one to
answer to but its parent, and a message typed here would land in a conversation
that is not the one on screen — so the composer is disabled while this view is
shown and the way back is one key away.

The body is a ``ChatLog`` of its own, fed from the node's messages: the same
blocks, the same tool gutters and the same collapsed reasoning as the main chat,
because a subagent's transcript is the same kind of thing. What the header adds is
the part the chat cannot know: where the node hangs, what it was asked to do, how
long it took and how it ended.

The transcript is rebuilt from the node's messages on every step, not streamed
delta by delta. That is deliberate: a subagent is snapshotted per step, and a view
that showed half a sentence the parent has not received yet would be showing a
draft of something that may never be said.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai.messages import ModelMessagesTypeAdapter
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.models.node_status import NodeStatus
from src.ui.widgets.chat_log import ChatLog

STATUS_STYLE = {
    NodeStatus.RUNNING: ('●', 'bold yellow', 'running'),
    NodeStatus.DONE: ('✓', 'green', 'done'),
    NodeStatus.FAILED: ('✖', 'bold red', 'failed'),
    NodeStatus.CANCELLED: ('◼', 'dim', 'cancelled'),
}
"""Mark, style and word for each state. The word matters: the mark is decoration."""


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


class AgentView(Vertical):
    """The replaced chat: one subagent, read-only."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._node: AgentNode | None = None
        self._parent_label = ''
        self._has_parent = False
        self._painted: tuple[str, str, bytes] | None = None
        self._paint_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        yield Static(Text(''), id='agent-header')
        yield ChatLog(id='agent-chat')

    @property
    def node(self) -> AgentNode | None:
        return self._node

    @property
    def transcript(self) -> ChatLog:
        return self.query_one('#agent-chat', ChatLog)

    # --- painting ----------------------------------------------------------

    async def show(self, node: AgentNode, *, parent_label: str = '', has_parent: bool = False) -> None:
        """Paints the node. The parent's label is what the way-back line says."""
        self._node = node
        self._parent_label = parent_label
        self._has_parent = has_parent
        self._painted = None
        await self._paint()

    async def refresh_node(self, node: AgentNode | None = None) -> None:
        """Repaints when the node moved on.

        The signature tracks the transcript and state, so changes within an
        existing message are not missed and clock-only updates do not rebuild it.
        """
        if node is not None and self._node is not None and node.id == self._node.id:
            self._node = node
        await self._paint()

    async def _paint(self) -> None:
        async with self._paint_lock:
            node = self._node
            if node is None:
                return
            header = build_agent_header(node, parent_label=self._parent_label, has_parent=self._has_parent)
            self.query_one('#agent-header', Static).update(header)
            messages = ModelMessagesTypeAdapter.dump_json(node.messages)
            status = node.status
            signature = (node.id, str(status), messages)
            if signature == self._painted:
                return
            chat = self.transcript
            await chat.clear()
            if messages != b'[]':
                await chat.render_history(list(ModelMessagesTypeAdapter.validate_json(messages)), from_agent=True)
                chat.finalize(close_pending=status.finished)
            elif status is NodeStatus.RUNNING:
                await chat.add_notice('Waiting for the first step…', style='dim')
            self._painted = signature

    async def repaint(self) -> None:
        """Theme change: the header and the blocks carry the old palette."""
        self._painted = None
        await self._paint()
        self.transcript.repaint()


__all__ = ['AgentView', 'build_agent_header', 'elapsed_text']
