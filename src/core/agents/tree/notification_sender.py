"""The NotificationSender value of the agents layer."""

from __future__ import annotations

from typing import Protocol


class NotificationSender(Protocol):
    """Only the sender identity is needed, never its run or conversation."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...
