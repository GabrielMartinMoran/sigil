"""A rendered inter-agent envelope.

Parent instructions use this envelope as steering in the child's history.
Informational notices live in ``Notifications`` instead; they are never enqueued
as user instructions. Final async reports have their own delivery mechanism.
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
