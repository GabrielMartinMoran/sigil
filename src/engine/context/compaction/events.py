"""Content-free maintenance records, persisted with the response they preceded."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

POLICY_VERSION = 2
RECORD_KEY = 'sigil_context'


class CompactionReason(StrEnum):
    OPERATIONAL = 'operational'
    BUDGET = 'budget'
    EMERGENCY = 'emergency'


@dataclass(frozen=True, slots=True)
class CompactionEvent:
    step: int
    reason: CompactionReason = CompactionReason.OPERATIONAL
    policy_version: int = POLICY_VERSION
    reasoning_kept: int = 0
    max_lod: int = 0
    unmet: tuple[str, ...] = ()
    detail: Mapping[str, int] = field(default_factory=dict)
    request_id: str | None = None
    policy: Mapping[str, Any] = field(default_factory=dict)
    window: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, 'detail', MappingProxyType(dict(self.detail)))
        policy = dict(self.policy)
        for key in ('lod_ages', 'pinned_tools'):
            if key in policy:
                policy[key] = tuple(policy[key])
        object.__setattr__(self, 'policy', MappingProxyType(policy))

    def as_row(self) -> dict[str, Any]:
        return {
            'step': self.step,
            'reason': str(self.reason),
            'policy_version': self.policy_version,
            'reasoning_kept': self.reasoning_kept,
            'max_lod': self.max_lod,
            'unmet': list(self.unmet),
            'detail': dict(self.detail),
            'request_id': self.request_id,
            'policy': dict(self.policy),
            'window': self.window,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CompactionEvent:
        return cls(**{**row, 'reason': CompactionReason(row['reason']), 'unmet': tuple(row.get('unmet', ()))})


@dataclass(frozen=True, slots=True)
class CompactionState:
    """Monotonic step frontiers and content-free discarded-part identities."""

    reasoning_cutoff: int = 0
    lod_cutoffs: tuple[int, int, int] = (0, 0, 0)
    dropped_parts: tuple[str, ...] = ()
    evaluated_steps: int = 0

    def as_row(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> CompactionState:
        return cls(
            reasoning_cutoff=int(row['reasoning_cutoff']),
            lod_cutoffs=tuple(row['lod_cutoffs']),
            dropped_parts=tuple(row.get('dropped_parts', ())),
            evaluated_steps=int(row['evaluated_steps']),
        )
