"""One agent in the tree of a session.

The node is the durable half of a subagent: the profile it ran, the task it was
given, what it answered, and the conversation it had. It is *not* the run: the
run is an ``AgentRun`` that lives for a few seconds and cannot be written to disk,
so the node keeps the messages the run produced instead, and the live object in a
field that is never serialized.

Why the transcript is kept at all: the UI navigates into a subagent to read what
it did, and it has to be able to do that for a node that finished an hour ago, in
a session that was closed and reopened. Keeping only the final message would make
the tree a report; keeping the messages makes it a place you can look at.

``started_at`` is an epoch float and not an ISO string: elapsed time is what the
tree paints, and a number needs no parsing on every repaint. It survives the JSON
round trip unchanged.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.models.channel import UNIDIRECTIONAL, ChannelPolicy, parse_channel
from src.engine.agents.tree.models.node_status import NodeStatus

ROOT_TASK = 'Session agent'
"""What the root node says where a subagent says the task it was given."""


def _id() -> str:
    return uuid.uuid4().hex


@dataclass(eq=False)
class AgentNode:
    """A node of the session tree: the root agent, or a subagent under it.

    Identity, not value: two nodes are the same node when they are the same object.
    Value equality would compare the transcripts, which is expensive and wrong — an
    empty node and a node that has not started yet are not the same one.
    """

    id: str
    """Stable id of the node. The tree and the UI address nodes by it."""

    name: str
    """Profile the node runs, by name. The label is derived from it."""

    role: AgentRole
    """``agent`` only for the root; everything else was summoned."""

    parent_id: str | None
    """Who summoned this node. ``None`` for the root, and only for the root."""

    depth: int
    """Distance to the root. The root is 0; a subagent of a subagent is 2."""

    task: str = ''
    """What the node was asked to do. Empty for the root."""

    status: NodeStatus = NodeStatus.RUNNING
    mode: str = 'sync'
    """``sync`` or ``async``: how its parent is waiting for the answer."""

    model: str = ''
    """The model it runs on, as a label. It is the parent's model by default."""

    messages: list[ModelMessage] = field(default_factory=list)
    """Everything the node's run produced, in order. The root's lives in the session."""

    result: str | None = None
    """The message it sent back when it finished."""

    error: str | None = None
    """Why it failed, when it did."""

    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    runs: int = 0
    """How many times this node has been started or resumed. Shown in the tree."""

    delivered: bool = True
    """Whether the parent has already read this node's answer.

    ``False`` on a finished node means the message is waiting: nobody was in a turn
    when it arrived. It is written to disk with the rest of the tree, so a message
    that arrives while the app is closing is retaken on the next open instead of
    disappearing with the process.
    """

    channel: ChannelPolicy = UNIDIRECTIONAL
    """Which way messages may travel for this node. Copied from its profile."""

    max_steps: int | None = None
    """The budget of its run, or ``None`` when the global one applies."""

    budget_exhausted: bool = False
    """Whether its last run ended because it used up its steps.

    It finished with an answer, so the node is ``done`` and the parent can continue
    — and it can also resume it with `summon(id=…)`, which is exactly the case this
    flag exists to make visible.
    """

    run: Any = field(default=None, repr=False, compare=False)
    """The live ``AgentRun`` while the node works. Never serialized."""

    # --- what the UI reads -------------------------------------------------

    @property
    def label(self) -> str:
        """Display name: capitalized, with the separators turned into spaces.

        Both separators, because a profile is named with dashes in some trees and with
        underscores in others, and a list of forty `Pas_Developer` rows reads like a
        variable name instead of a name.
        """
        return self.name.replace('-', ' ').replace('_', ' ').title() if self.name else 'Agent'

    @property
    def short_id(self) -> str:
        return self.id[:8]

    @property
    def is_root(self) -> bool:
        return self.parent_id is None

    @property
    def elapsed(self) -> float:
        """Seconds the node worked, or has been working."""
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)

    def start(self) -> None:
        """Marks the node as working, born or resumed."""
        self.status = NodeStatus.RUNNING
        self.finished_at = None
        self.error = None
        self.runs += 1

    def finish(self, result: str, *, exhausted: bool = False) -> None:
        """Marks the node as done with the message it sends back."""
        self.result = result
        self.status = NodeStatus.DONE
        self.finished_at = time.time()
        self.budget_exhausted = exhausted

    def fail(self, error: str) -> None:
        """Marks the node as failed. The error travels to the parent."""
        self.error = error
        self.status = NodeStatus.FAILED
        self.finished_at = time.time()

    def cancel(self) -> None:
        """Marks the node as stopped from outside."""
        self.status = NodeStatus.CANCELLED
        self.finished_at = time.time()

    # --- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The node as JSON-safe data. The live run is left out."""
        data: dict[str, Any] = {
            'id': self.id,
            'name': self.name,
            'role': str(self.role),
            'parent_id': self.parent_id,
            'depth': self.depth,
            'task': self.task,
            'status': str(self.status),
            'mode': self.mode,
            'model': self.model,
            'result': self.result,
            'error': self.error,
            'started_at': self.started_at,
            'finished_at': self.finished_at,
            'runs': self.runs,
            'delivered': self.delivered,
            'channel': {
                'mode': str(self.channel.mode),
                'parent': self.channel.to_parent,
                'siblings': list(self.channel.to_siblings),
            },
            'max_steps': self.max_steps,
            'budget_exhausted': self.budget_exhausted,
        }
        if self.messages:
            data['messages'] = ModelMessagesTypeAdapter.dump_python(self.messages, mode='json')
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentNode | None:
        """Rebuilds a node, or ``None`` when the stored one cannot be trusted.

        A session file is written by an older Sigil as often as by this one, and
        it is edited by hand more often than that. A node that does not read back
        is dropped: losing one subagent of a saved tree is better than losing the
        conversation that tree belongs to.
        """
        try:
            node_id = str(data['id'])
            name = str(data['name'])
            parent = data.get('parent_id')
            node = cls(
                id=node_id,
                name=name,
                role=AgentRole(str(data.get('role') or AgentRole.SUBAGENT)),
                parent_id=None if parent is None else str(parent),
                depth=int(data.get('depth') or 0),
                task=str(data.get('task') or ''),
                status=NodeStatus(str(data.get('status') or NodeStatus.RUNNING)),
                mode=str(data.get('mode') or 'sync'),
                model=str(data.get('model') or ''),
                result=None if data.get('result') is None else str(data.get('result')),
                error=None if data.get('error') is None else str(data.get('error')),
                started_at=float(data.get('started_at') or time.time()),
                finished_at=None if data.get('finished_at') is None else float(data['finished_at']),
                runs=int(data.get('runs') or 0),
                delivered=bool(data.get('delivered', True)),
                channel=_channel(data.get('channel')),
                max_steps=None if data.get('max_steps') is None else int(data['max_steps']),
                budget_exhausted=bool(data.get('budget_exhausted')),
            )
        except KeyError, TypeError, ValueError:
            return None
        node.messages = _messages(data.get('messages'))
        return node


def _channel(raw: Any) -> ChannelPolicy:
    """A stored channel, falling back to the default on anything unreadable.

    Unlike the frontmatter, a stored tree is not hand-written intent: it is a
    record. An unreadable channel there is a reason to keep the node, not to lose
    the conversation it belongs to.
    """
    if isinstance(raw, Mapping):
        try:
            return parse_channel(raw)
        except Exception:  # noqa: BLE001 - a stored record is not a declaration
            return UNIDIRECTIONAL
    return UNIDIRECTIONAL


def _messages(raw: Any) -> list[ModelMessage]:
    """Best-effort read of a stored transcript: an unreadable one is empty."""
    if not raw:
        return []
    try:
        return list(ModelMessagesTypeAdapter.validate_python(raw))
    except Exception:  # noqa: BLE001 - a broken transcript must not break the session
        return []


__all__ = ['ROOT_TASK', 'AgentNode']
