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

from src.core.agents.tree.models.agent_node import AgentNode
from src.core.agents.tree.models.node_status import NodeStatus
from src.ui.widgets.chat.chat_log import ChatLog
from src.ui.widgets.panels.agent_view_text import build_agent_header

"""Mark, style and word for each state. The word matters: the mark is decoration."""


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
                await chat.render_history(
                    list(ModelMessagesTypeAdapter.validate_json(messages)),
                    subagents=self.app.tree_nodes() if hasattr(self.app, 'tree_nodes') else (),
                )
                chat.finalize(close_pending=status.finished)
            elif status is NodeStatus.RUNNING:
                await chat.add_notice('Waiting for the first step…', style='dim')
            self._painted = signature

    async def repaint(self) -> None:
        """Theme change: the header and the blocks carry the old palette."""
        self._painted = None
        await self._paint()
        self.transcript.repaint()


__all__ = ['AgentView']
