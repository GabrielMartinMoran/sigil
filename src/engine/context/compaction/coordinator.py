"""Pure planning over the original transcript and the persisted decision.

The projection is a function of three things: the transcript, the stored decision and
the policy. Nothing here calls a model or reads a clock, so a resume, a rollback and a
fork rebuild exactly the same view from the same history.
"""

from __future__ import annotations

import dataclasses
import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    NativeToolCallPart,
    NativeToolReturnPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

from src.engine.context.compaction.clock import step_count
from src.engine.context.compaction.events import (
    CompactionEvent,
    CompactionReason,
    CompactionState,
)
from src.engine.context.compaction.identity import boundary_index, part_key
from src.engine.context.compaction.policy import DEFAULT_POLICY, CompactionPolicy
from src.engine.context.compaction.records import recorded_state
from src.engine.context.strategy.rewriting import accepts_replayed_reasoning
from src.engine.context.tokens import estimate_tokens
from src.engine.tooling.compaction import (
    GENERIC_COMPACTOR,
    ToolCompactor,
    contains_binary,
    encoded,
    shorten,
)

PolicyLookup = Callable[[str], ToolCompactor | None]


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    state: CompactionState = field(default_factory=CompactionState)
    events: tuple[CompactionEvent, ...] = ()
    recorded_events: int = 0
    """How many of ``events`` came from the transcript instead of this projection."""
    exceptions: tuple[str, ...] = ()
    effective_policy: Mapping[str, Any] = field(default_factory=dict)


def plan(
    messages: Sequence[ModelMessage],
    *,
    policy: CompactionPolicy = DEFAULT_POLICY,
    lookup: PolicyLookup | None = None,
    pressure: bool = False,
    provider: str | None = None,
) -> CompactionPlan:
    """Replay the recorded decision and, when requested, extend it in one batch.

    Ordinary operation leaves the history unchanged. The caller requests a batch under
    pressure or when an instruction change offers an opportunistic boundary.
    """
    state, recorded = recorded_state(messages)
    events = list(recorded)
    if pressure:
        state, event = _compact(messages, state, policy, provider)
        if event is not None:
            events.append(event)
    return CompactionPlan(
        state=state,
        events=tuple(events),
        recorded_events=len(recorded),
        effective_policy={**asdict(policy)},
        exceptions=('native_tools_preserved',)
        if pressure
        and any(
            isinstance(part, (NativeToolCallPart, NativeToolReturnPart))
            for message in messages
            for part in message.parts
        )
        else (),
    )


def _compact(
    messages: Sequence[ModelMessage],
    state: CompactionState,
    policy: CompactionPolicy,
    provider: str | None,
) -> tuple[CompactionState, CompactionEvent | None]:
    """Reduce every eligible reasoning block and tool call older than the protected tail."""
    start = boundary_index(messages, state.summary_boundary)
    reasoning = [
        (part_key(message, part, offset), estimate_tokens(part.content or ''))
        for index, message in enumerate(messages)
        if index >= start
        for offset, part in enumerate(message.parts)
        if isinstance(part, ThinkingPart)
        and part_key(message, part, offset) not in state.dropped_parts
        and accepts_replayed_reasoning(part, provider)
    ]
    calls = [
        part.tool_call_id
        for index, message in enumerate(messages)
        if index >= start
        for part in message.parts
        if isinstance(part, ToolCallPart) and part.tool_call_id
    ]
    identities = Counter(key for key, _ in reasoning)
    removable = [
        (key, cost)
        for key, cost in reasoning[: max(0, len(reasoning) - policy.recent_reasoning_blocks)]
        if identities[key] == 1
    ]
    older_calls = calls[: max(0, len(calls) - policy.recent_tool_calls)]
    updated = replace(
        state,
        dropped_parts=tuple(dict.fromkeys((*state.dropped_parts, *(key for key, _ in removable)))),
        compacted_calls=tuple(dict.fromkeys((*state.compacted_calls, *older_calls))),
    )
    if updated == state:
        return state, None
    return updated, CompactionEvent(
        step=step_count(messages),
        reason=CompactionReason.DETERMINISTIC,
        reasoning_kept=len(reasoning) - len(removable),
        compacted_calls=len(updated.compacted_calls),
        detail={
            'reasoning_before': len(reasoning),
            'reasoning_after': len(reasoning) - len(removable),
            'reasoning_tokens_before': sum(cost for _, cost in reasoning),
            'reasoning_tokens_after': sum(
                cost for key, cost in reasoning if key not in {removed for removed, _ in removable}
            ),
            'calls_before': len(calls),
            'calls_after': len(calls) - len(older_calls),
        },
    )


def apply(
    messages: Sequence[ModelMessage],
    planned: CompactionPlan,
    *,
    lookup: PolicyLookup | None = None,
    provider: str | None = None,
) -> tuple[list[ModelMessage], int]:
    """Project the original parts; protocol ids, outcome, order and binaries survive."""
    state = planned.state
    start = boundary_index(messages, state.summary_boundary)
    result: list[ModelMessage] = []
    reduced = 0
    if state.summary and start:
        result.append(ModelRequest(parts=[CompactionPart(content=state.summary)]))
    for index, message in enumerate(messages):
        if index < start:
            continue
        parts = []
        for part_index, part in enumerate(message.parts):
            if part_key(message, part, part_index) in state.dropped_parts:
                continue
            if isinstance(part, ThinkingPart) and not accepts_replayed_reasoning(part, provider):
                continue
            new_part = _compacted_part(part, state, lookup)
            reduced += new_part != part
            parts.append(new_part)
        if message.parts and not parts:
            continue
        rewritten = message if parts == list(message.parts) else dataclasses.replace(message, parts=parts)
        result.append(rewritten)
    return result, reduced


def _compacted_part(part: Any, state: CompactionState, lookup: PolicyLookup | None) -> Any:
    if not isinstance(part, (ToolCallPart, ToolReturnPart)):
        return part
    if (part.tool_call_id or '') not in state.compacted_calls:
        return part
    compactor = _compactor_for(part.tool_name, lookup)
    if isinstance(part, ToolCallPart):
        args = _args_dict(part)
        if args is None:
            return part
        reduced = compactor.compact_input(args)
        return part if encoded(reduced) == encoded(args) else replace(part, args=reduced)
    # Recovery errors are plain text, even for normally structured own tools.
    if part.outcome != 'success' and isinstance(part.content, str):
        return replace(part, content=shorten(part.content, 600, label='error'))
    if contains_binary(part.content) and compactor is GENERIC_COMPACTOR:
        return part
    reduced = compactor.compact_output(part.content)
    return part if encoded(reduced) == encoded(part.content) else replace(part, content=reduced)


def exceptions(
    messages: Sequence[ModelMessage], planned: CompactionPlan, lookup: PolicyLookup | None
) -> tuple[str, ...]:
    """Content-free reasons a compacted value could not meet its requested form."""
    reasons: set[str] = set()
    for message in messages:
        for part in message.parts:
            if not isinstance(part, (ToolCallPart, ToolReturnPart)):
                continue
            if (part.tool_call_id or '') not in planned.state.compacted_calls:
                continue
            compactor = _compactor_for(part.tool_name, lookup)
            value = _args_dict(part) if isinstance(part, ToolCallPart) else part.content
            if value is None:
                reasons.add('malformed_arguments')
            elif contains_binary(value) and compactor is GENERIC_COMPACTOR:
                reasons.add('opaque_binary_or_multimodal')
            elif compactor is GENERIC_COMPACTOR:
                compacted = (
                    compactor.compact_input(value)
                    if isinstance(part, ToolCallPart)
                    else compactor.compact_output(value)
                )
                size = len(compacted) if isinstance(compacted, str) else len(encoded(compacted))
                if size > 500:
                    reasons.add('indispensable_generic_metadata')
    return tuple(sorted(reasons))


def _compactor_for(tool_name: str, lookup: PolicyLookup | None) -> ToolCompactor:
    return (lookup(tool_name) if lookup is not None else None) or GENERIC_COMPACTOR


def _args_dict(part: ToolCallPart) -> dict[str, Any] | None:
    if part.args is None:
        return {}
    if isinstance(part.args, dict):
        return part.args
    try:
        parsed = json.loads(part.args)
    except ValueError, TypeError:
        return None
    return parsed if isinstance(parsed, dict) else None


__all__ = [
    'CompactionPlan',
    'PolicyLookup',
    'apply',
    'exceptions',
    'plan',
]
