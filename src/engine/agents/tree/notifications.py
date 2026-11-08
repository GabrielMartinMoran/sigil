"""Ordered notification state, independent of steering and conversation history."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Protocol, TypedDict


class NotificationSender(Protocol):
    """Only the sender identity is needed, never its run or conversation."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...


class NotificationRecord(TypedDict):
    sequence: int
    sender_id: str
    sender_name: str
    receiver_id: str
    text: str
    acknowledged: bool
    created_at: float


def _field[T](data: Mapping[str, object], name: str, expected: type[T]) -> T:
    value = data.get(name)
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ValueError(f'{name} must be {expected.__name__}.')
    return value


def _timestamp(data: Mapping[str, object]) -> float:
    value = data.get('created_at')
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError('created_at must be a numeric timestamp.')
    return float(value)


@dataclass(frozen=True, slots=True)
class AgentNotification:
    """One immutable notice. ACK confirms receipt, not agreement or resolution."""

    sequence: int
    sender_id: str
    sender_name: str
    receiver_id: str
    text: str
    acknowledged: bool = False
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError('sequence must be a positive integer.')
        for name, value in (
            ('sender_id', self.sender_id),
            ('sender_name', self.sender_name),
            ('receiver_id', self.receiver_id),
            ('text', self.text),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be a nonempty string.')
        if type(self.acknowledged) is not bool:
            raise ValueError('acknowledged must be a boolean.')
        if type(self.created_at) not in (int, float) or not math.isfinite(self.created_at) or self.created_at < 0:
            raise ValueError('created_at must be a finite, nonnegative timestamp.')

    @property
    def id(self) -> str:
        """Stable within its session, including across saves and resumed runs."""
        return f'n-{self.sequence}'

    def to_dict(self) -> NotificationRecord:
        return {
            'sequence': self.sequence,
            'sender_id': self.sender_id,
            'sender_name': self.sender_name,
            'receiver_id': self.receiver_id,
            'text': self.text,
            'acknowledged': self.acknowledged,
            'created_at': self.created_at,
        }

    @classmethod
    def from_dict(cls, data: object) -> AgentNotification:
        if not isinstance(data, Mapping):
            raise TypeError('Invalid notification record: expected a mapping.')
        try:
            return cls(
                sequence=_field(data, 'sequence', int),
                sender_id=_field(data, 'sender_id', str),
                sender_name=_field(data, 'sender_name', str),
                receiver_id=_field(data, 'receiver_id', str),
                text=_field(data, 'text', str),
                acknowledged=_field(data, 'acknowledged', bool),
                created_at=_timestamp(data),
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f'Invalid notification {data.get("sequence", "?")}: {error}') from error


class Notifications:
    """The session's log. Only the addressed execution can acknowledge a notice."""

    def __init__(self, entries: Sequence[AgentNotification] = ()) -> None:
        self._entries: dict[str, AgentNotification] = {}
        for entry in sorted(entries, key=lambda entry: entry.sequence):
            if entry.id in self._entries:
                raise ValueError(f'Duplicate notification id {entry.id!r}.')
            self._entries[entry.id] = entry
        self._sequence: int = max((entry.sequence for entry in entries), default=0)

    def all(self) -> tuple[AgentNotification, ...]:
        return tuple(self._entries.values())

    def for_receiver(self, receiver_id: str) -> tuple[AgentNotification, ...]:
        return tuple(entry for entry in self._entries.values() if entry.receiver_id == receiver_id)

    def publish(self, sender: NotificationSender, receiver_id: str, text: str) -> AgentNotification:
        entry = AgentNotification(self._sequence + 1, sender.id, sender.name, receiver_id, text.strip())
        self._entries[entry.id] = entry
        self._sequence = entry.sequence
        return entry

    def acknowledge(self, receiver_id: str, notification_ids: Sequence[str]) -> None:
        """Validate the entire batch before changing anything; retries are harmless."""
        if not notification_ids:
            raise ValueError('Provide at least one notification id from your table.')
        for notification_id in notification_ids:
            entry = self._entries.get(notification_id)
            if entry is None or entry.receiver_id != receiver_id:
                raise ValueError(f'Notification {notification_id!r} is not in your notification table.')
        for notification_id in dict.fromkeys(notification_ids):
            entry = self._entries[notification_id]
            if not entry.acknowledged:
                self._entries[notification_id] = replace(entry, acknowledged=True)

    @classmethod
    def from_dict(cls, entries: Sequence[object]) -> Notifications:
        return cls([AgentNotification.from_dict(entry) for entry in entries])
