"""The ReplayReport value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class ReplayReport:
    requests: int
    input_tokens: int
    common_prefix_tokens: int
    compactions: int
    summary_requests: int
    blocked: bool

    def input_cost(self, *, uncached: Decimal, cached: Decimal) -> Decimal:
        """Hypothetical input-only cost per million tokens; no writes or auxiliary calls."""
        if min(uncached, cached) < 0:
            raise ValueError('prices must be non-negative')
        return (
            (self.input_tokens - self.common_prefix_tokens) * uncached + self.common_prefix_tokens * cached
        ) / 1_000_000
