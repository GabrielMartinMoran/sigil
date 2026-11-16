"""The NotificationRecord value of the agents layer."""

from __future__ import annotations

from typing import TypedDict


class NotificationRecord(TypedDict):
    sequence: int
    sender_id: str
    sender_name: str
    receiver_id: str
    text: str
    acknowledged: bool
    created_at: float
