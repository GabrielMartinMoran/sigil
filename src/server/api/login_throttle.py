"""Delay after repeated failed logins."""

from __future__ import annotations

import time

from src.server.api.auth import FAILURE_WINDOW_SECONDS, MAX_DELAY_SECONDS, MAX_FAILURES_PER_WINDOW


class LoginThrottle:
    """Growing delay and a hard stop for repeated wrong passwords, per client."""

    def __init__(
        self,
        *,
        window_seconds: float = FAILURE_WINDOW_SECONDS,
        max_failures: int = MAX_FAILURES_PER_WINDOW,
        max_delay: float = MAX_DELAY_SECONDS,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_failures = max_failures
        self.max_delay = max_delay
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        stamps = [stamp for stamp in self._failures.get(key, []) if now - stamp < self.window_seconds]
        if stamps:
            self._failures[key] = stamps
        else:
            self._failures.pop(key, None)
        return stamps

    def blocked_for(self, key: str, *, now: float | None = None) -> float:
        """Seconds the caller has to wait before trying again, or ``0``."""
        current = time.time() if now is None else now
        stamps = self._recent(key, current)
        if len(stamps) < self.max_failures:
            return 0.0
        return max(0.0, self.window_seconds - (current - stamps[0]))

    def delay_for(self, key: str, *, now: float | None = None) -> float:
        """How long a rejected attempt takes to answer: it grows with the count."""
        current = time.time() if now is None else now
        count = len(self._recent(key, current))
        return min(self.max_delay, 0.25 * count)

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        self._failures.setdefault(key, []).append(current)

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
