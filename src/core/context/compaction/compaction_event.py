"""The CompactionEvent value of the context layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from src.core.context.compaction.compaction_reason import CompactionReason

POLICY_VERSION = 4


_LEGACY_REASONS: dict[str, str] = {
    'operational': CompactionReason.DETERMINISTIC,
    'budget': CompactionReason.EMERGENCY,
}


@dataclass(frozen=True, slots=True)
class CompactionEvent:
    step: int
    reason: CompactionReason = CompactionReason.DETERMINISTIC
    policy_version: int = POLICY_VERSION
    reasoning_kept: int = 0
    compacted_calls: int = 0
    summary_chars: int = 0
    unmet: tuple[str, ...] = ()
    detail: Mapping[str, int] = field(default_factory=dict)
    request_id: str | None = None
    policy: Mapping[str, Any] = field(default_factory=dict)
    window: int | None = None
    triggers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, 'detail', MappingProxyType(dict(self.detail)))
        policy = dict(self.policy)
        for key in ('pinned_tools',):
            if key in policy:
                policy[key] = tuple(policy[key])
        object.__setattr__(self, 'policy', MappingProxyType(policy))

    def as_row(self) -> dict[str, Any]:
        return {
            'step': self.step,
            'reason': str(self.reason),
            'policy_version': self.policy_version,
            'reasoning_kept': self.reasoning_kept,
            'compacted_calls': self.compacted_calls,
            'summary_chars': self.summary_chars,
            'unmet': list(self.unmet),
            'detail': dict(self.detail),
            'request_id': self.request_id,
            'policy': dict(self.policy),
            'window': self.window,
            'triggers': list(self.triggers),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CompactionEvent:
        """Read a stored event, including the ones written before the current shape."""
        return cls(
            step=int(row['step']),
            reason=CompactionReason(_LEGACY_REASONS.get(row['reason'], row['reason'])),
            policy_version=int(row.get('policy_version', POLICY_VERSION)),
            reasoning_kept=int(row.get('reasoning_kept', 0)),
            compacted_calls=int(row.get('compacted_calls', 0)),
            summary_chars=int(row.get('summary_chars', 0)),
            unmet=tuple(row.get('unmet', ())),
            detail=dict(row.get('detail', {})),
            request_id=row.get('request_id'),
            policy=dict(row.get('policy', {})),
            window=row.get('window'),
            triggers=tuple(row.get('triggers', ())),
        )
