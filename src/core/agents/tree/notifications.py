"""Ordered notification state, independent of steering and conversation history."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from src.core.agents.tree.agent_notification import AgentNotification
from src.core.agents.tree.notification_sender import NotificationSender


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
