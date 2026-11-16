"""The RetryNotice value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryNotice:
    """Data handed to the notifier when a retry is about to happen."""

    attempt: int
    """Number of the attempt that just failed (starts at 1)."""
    next_attempt: int
    max_attempts: int
    delay: float
    """Seconds to wait before the next attempt."""
    reason: str
