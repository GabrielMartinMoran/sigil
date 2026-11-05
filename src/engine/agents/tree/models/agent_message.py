"""One message between two agents of the session.

Two kinds exist, and the difference is the whole point of the channel design:

- **Instruction.** From a parent to its child. It arrives the way a message typed
  in the chat arrives — injected into the run, read as the next thing to do.
- **Notice.** From a child to its parent, or between siblings. It is information:
  "I found something", "I am blocked", "stop looking there". The receiver reads it
  and decides. It is not a command, and the prompt says so in the block that lists
  the pending ones.

A message is delivered one way: into a live run, with ``enqueue``, so the model reads
it before its next request. A node that is not running has nothing to read with, and
the caller is told so instead of leaving the message in a queue nobody promised to
drain. What arrives on its own — a subagent's answer — is different: that travels as
an envelope, and it waits in the summoner's queue when no turn is open.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

MessageKind = Literal['instruction', 'notice']
"""How the receiver has to read it: something to do, or something to know."""


@dataclass(slots=True)
class AgentMessage:
    """A message from one node of the tree to another."""

    sender_id: str
    """Node id of whoever sent it. The root's id for the agent the user talks to."""

    sender_name: str
    """Profile name of the sender, for the line the receiver reads."""

    kind: MessageKind = 'notice'
    text: str = ''
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: float = field(default_factory=time.time)

    @property
    def short_id(self) -> str:
        return self.id[:8]

    def render(self) -> str:
        """The message as the receiver reads it, envelope and all.

        The header is not decoration: a notice and an instruction have to be told
        apart at the first line, or a subagent takes "the tests fail in two places"
        as an order to go and fix them. The sender and its node id are what let the
        receiver answer, when its channel allows it.
        """
        label = 'instruction from your parent' if self.kind == 'instruction' else 'notice'
        return (
            f'[message · {label} · from {self.sender_name} (#{self.sender_id[:8]})]\n'
            f'{self.text.strip()}\n'
            f'[end of message · {self.kind}]'
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            'id': self.id,
            'sender_id': self.sender_id,
            'sender_name': self.sender_name,
            'kind': self.kind,
            'text': self.text,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentMessage | None:
        """Rebuilds one message, or ``None`` when the stored one is unusable."""
        try:
            sender_id = str(data['sender_id'])
            text = str(data.get('text') or '')
        except KeyError, TypeError:
            return None
        kind: MessageKind = 'instruction' if data.get('kind') == 'instruction' else 'notice'
        if not text.strip():
            return None
        return cls(
            sender_id=sender_id,
            sender_name=str(data.get('sender_name') or 'agent'),
            kind=kind,
            text=text,
            id=str(data.get('id') or uuid.uuid4().hex),
            created_at=float(data.get('created_at') or time.time()),
        )


__all__ = ['AgentMessage', 'MessageKind']
