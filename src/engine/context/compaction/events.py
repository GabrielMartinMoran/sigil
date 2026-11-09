"""Content-free maintenance records, persisted with the response they preceded."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

POLICY_VERSION = 4
"""Shape of the persisted decision. Bumped when a stored record needs migration."""

LEGACY_POLICY_VERSION = 2
"""Records written before compaction stopped using levels of detail."""

RECORD_KEY = 'sigil_context'


class CompactionReason(StrEnum):
    DETERMINISTIC = 'deterministic'
    """Old tool payloads and reasoning reduced in one batch, without a model call."""

    SUMMARY = 'summary'
    """An older span replaced in the view by a structured model-written checkpoint."""

    EMERGENCY = 'emergency'
    """The projection still does not fit the hard safety budget."""


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
        )


@dataclass(frozen=True, slots=True)
class CompactionState:
    """What the model view must no longer carry, plus the summary that replaces it.

    Identities are content-free: a part key or a tool call id, never the payload. That
    is what lets a resume, a rollback and a fork rebuild the same projection from the
    original transcript without storing a decision per message.
    """

    dropped_parts: tuple[str, ...] = ()
    """Reasoning parts removed from the view, by stable part key."""

    compacted_calls: tuple[str, ...] = ()
    """Tool calls whose arguments and result are sent in their compacted form."""

    summary: str | None = None
    """The checkpoint that replaces everything before ``summary_boundary``."""

    summary_boundary: str | None = None
    """Part key of the first message the summary does not replace."""

    image_removals: tuple[tuple[str, str], ...] = ()
    """Stable image occurrence keys and the reason each left the view."""

    evaluated_steps: int = 0

    def as_row(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> CompactionState:
        return cls(
            dropped_parts=tuple(row.get('dropped_parts', ())),
            compacted_calls=tuple(row.get('compacted_calls', ())),
            summary=row.get('summary') or None,
            summary_boundary=row.get('summary_boundary') or None,
            image_removals=tuple((key, reason) for key, reason in row.get('image_removals', ())),
            evaluated_steps=int(row.get('evaluated_steps', 0)),
        )
