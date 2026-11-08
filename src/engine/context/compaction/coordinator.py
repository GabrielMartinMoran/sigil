"""Pure coordinated planning over original transcripts and persisted frontiers."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.context.compaction.clock import step_count, step_of_message, tool_call_steps
from src.engine.context.compaction.events import (
    POLICY_VERSION,
    RECORD_KEY,
    CompactionEvent,
    CompactionReason,
    CompactionState,
)
from src.engine.context.compaction.policy import DEFAULT_POLICY, CompactionPolicy
from src.engine.context.strategy.rewriting import accepts_replayed_reasoning
from src.engine.context.tokens import estimate_tokens
from src.engine.tooling.lod import GENERIC_LOD, ToolLOD, ToolLODPolicy, contains_binary, encoded, limit_for

PolicyLookup = Callable[[str], ToolLODPolicy | None]


class ContextPolicyVersionError(ValueError):
    """A newer policy record needs an explicit migration, not silent reinterpretation."""


@dataclass(frozen=True, slots=True)
class CompactionPlan:
    events: tuple[CompactionEvent, ...] = ()
    lod: Mapping[str, ToolLOD] = field(default_factory=dict)
    reasoning_steps: int = DEFAULT_POLICY.reasoning_steps
    state: CompactionState = field(default_factory=CompactionState)
    recorded_events: int = 0
    exceptions: tuple[str, ...] = ()
    effective_policy: Mapping[str, Any] = field(default_factory=dict)

    def level_of(self, tool_call_id: str) -> ToolLOD:
        return self.lod.get(tool_call_id, ToolLOD.FULL)


def policies_from(mapping: Mapping[str, ToolLODPolicy]) -> PolicyLookup:
    return mapping.get


def recorded_state(messages: Sequence[ModelMessage]) -> tuple[CompactionState, tuple[CompactionEvent, ...]]:
    """Records live on responses: rollback/fork and SQLite transactions carry them together."""
    state = CompactionState()
    events: list[CompactionEvent] = []
    seen: set[str] = set()
    anchors = {part_key(m, part, index) for m in messages for index, part in enumerate(m.parts)}
    message_steps = step_of_message(messages)
    for index, message in enumerate(messages):
        record = (message.metadata or {}).get(RECORD_KEY)
        if not isinstance(record, dict):
            continue
        if record.get('anchor') and record['anchor'] not in anchors:
            continue
        if record.get('version') != POLICY_VERSION:
            raise ContextPolicyVersionError('Unsupported context policy record; explicit migration is required.')
        request_id = record.get('request_id')
        if request_id and request_id in seen:
            continue
        if request_id:
            seen.add(request_id)
        candidate = CompactionState.from_row(record['state'])
        if candidate.evaluated_steps > message_steps[index]:
            raise ContextPolicyVersionError('Context frontier points beyond its owning response.')
        state = candidate
        for row in record['events']:
            event = CompactionEvent.from_row(row)
            if event not in events:
                events.append(event)
    return state, tuple(events)


def plan(
    messages: Sequence[ModelMessage],
    *,
    policy: CompactionPolicy = DEFAULT_POLICY,
    lookup: PolicyLookup | None = None,
    pressure: bool = False,
    window: int | None = None,
    provider: str | None = None,
) -> CompactionPlan:
    """Replay only the unrecorded tail; configuration changes affect future boundaries.

    Legacy sessions have no decisions to recover. They are deterministically bootstrapped
    once under the current policy, marked as reconstructed in diagnostics, then recorded.
    """
    state, recorded = recorded_state(messages)
    events = list(recorded)
    steps = step_of_message(messages)
    # Evaluate at complete step boundaries, never between parallel calls and returns.
    ends = [index for index in range(1, len(messages)) if isinstance(messages[index], ModelResponse)]
    ends.append(len(messages))
    for end in ends:
        prefix = messages[:end]
        current = steps[end - 1] if end else 0
        if current <= state.evaluated_steps:
            continue
        state, event = _advance(prefix, current, state, policy, lookup, window, provider)
        if event is not None:
            events.append(event)
    if pressure:
        state, event = _maintain(
            messages, step_count(messages), state, policy, lookup, window, provider, CompactionReason.BUDGET
        )
        if event is not None:
            events.append(event)
    levels = _levels(messages, state)
    return CompactionPlan(
        tuple(events),
        levels,
        policy.reasoning_steps,
        state,
        len(recorded),
        effective_policy={**asdict(policy), 'pinned_tools': sorted(policy.pinned_tools)},
    )


def _reasoning(messages: Sequence[ModelMessage], cutoff: int, provider: str | None) -> list[tuple[int, int]]:
    steps = step_of_message(messages)
    return [
        (
            steps[index],
            sum(
                estimate_tokens(part.content or '')
                for part in message.parts
                if isinstance(part, ThinkingPart) and accepts_replayed_reasoning(part, provider)
            ),
        )
        for index, message in enumerate(messages)
        if isinstance(message, ModelResponse)
        and steps[index] > cutoff
        and any(isinstance(part, ThinkingPart) and accepts_replayed_reasoning(part, provider) for part in message.parts)
    ]


def _advance(
    messages: Sequence[ModelMessage],
    current: int,
    state: CompactionState,
    policy: CompactionPolicy,
    lookup: PolicyLookup | None,
    window: int | None,
    provider: str | None,
) -> tuple[CompactionState, CompactionEvent | None]:
    reasoning = _reasoning(messages, state.reasoning_cutoff, provider)
    count_hit = len(reasoning) >= policy.reasoning_trigger_steps
    size_hit = (
        window is not None and sum(cost for _, cost in reasoning) >= window * policy.reasoning_trigger_percent / 100
    )
    pending = pending_tool_chars(messages, state, current, policy, lookup)
    if count_hit or size_hit or pending >= policy.tool_trigger_chars:
        return _maintain(messages, current, state, policy, lookup, window, provider, CompactionReason.OPERATIONAL)
    return state, None


def pending_tool_chars(
    messages: Sequence[ModelMessage],
    state: CompactionState,
    current: int,
    policy: CompactionPolicy,
    lookup: PolicyLookup | None,
) -> int:
    before = _levels(messages, state)
    eligible = {key: policy.lod_for_age(current - step) for key, step in tool_call_steps(messages).items()}
    total = 0
    for message in messages:
        for index, part in enumerate(message.parts):
            if part_key(message, part, index) in state.dropped_parts:
                continue
            if not isinstance(part, (ToolCallPart, ToolReturnPart)):
                continue
            old, new = before.get(part.tool_call_id, ToolLOD.FULL), eligible.get(part.tool_call_id, ToolLOD.FULL)
            if new <= old:
                continue
            declaration = _policy_for(part.tool_name, lookup)
            value = _args_dict(part) if isinstance(part, ToolCallPart) else part.content
            compact = declaration.compact_input if isinstance(part, ToolCallPart) else declaration.compact_output
            if value is not None and not (declaration is GENERIC_LOD and contains_binary(value)):
                total += max(0, len(encoded(compact(value, old))) - len(encoded(compact(value, new))))
    return total


def _maintain(
    messages: Sequence[ModelMessage],
    current: int,
    state: CompactionState,
    policy: CompactionPolicy,
    lookup: PolicyLookup | None,
    window: int | None,
    provider: str | None,
    reason: CompactionReason,
) -> tuple[CompactionState, CompactionEvent | None]:
    reasoning = _reasoning(messages, state.reasoning_cutoff, provider)
    kept = list(reasoning)
    cap = None if window is None else window * policy.reasoning_max_percent // 100
    while kept and (len(kept) > policy.reasoning_steps or (cap is not None and sum(c for _, c in kept) > cap)):
        kept.pop(0)
    removed = len(reasoning) - len(kept)
    cutoff = reasoning[removed - 1][0] if removed else state.reasoning_cutoff
    cutoffs = _tool_frontiers(messages, current, state, policy, lookup)
    updated = replace(state, reasoning_cutoff=cutoff, lod_cutoffs=cutoffs)
    if updated == state:
        return state, None
    before, after = _levels(messages, state), _levels(messages, updated)
    progressed = removed or any(after[key] > before[key] for key in after)
    if not progressed:
        return state, None
    event = CompactionEvent(
        step=current,
        reason=reason,
        reasoning_kept=len(kept),
        max_lod=max(map(int, after.values()), default=0),
        detail={
            'reasoning_before': len(reasoning),
            'reasoning_after': len(kept),
            'reasoning_tokens_before': sum(c for _, c in reasoning),
            'reasoning_tokens_after': sum(c for _, c in kept),
            'compacted_calls': sum(level is not ToolLOD.FULL for level in after.values()),
        },
    )
    return updated, event


def _levels(messages: Sequence[ModelMessage], state: CompactionState) -> dict[str, ToolLOD]:
    return {
        key: ToolLOD(sum(step <= cutoff for cutoff in state.lod_cutoffs))
        for key, step in tool_call_steps(messages).items()
    }


def apply(
    messages: list[ModelMessage],
    plan_result: CompactionPlan,
    *,
    policy: CompactionPolicy = DEFAULT_POLICY,
    lookup: PolicyLookup | None = None,
    provider: str | None = None,
    with_positions: bool = False,
) -> tuple[list[ModelMessage], int]:
    """Project original parts; protocol ids, outcome, order and binary payloads survive."""
    reduced = 0
    result: list[ModelMessage] = []
    steps = step_of_message(messages)
    for index, message in enumerate(messages):
        parts = []
        removed = False
        for part_index, part in enumerate(message.parts):
            if part_key(message, part, part_index) in plan_result.state.dropped_parts:
                removed = True
                continue
            if isinstance(part, ThinkingPart) and (
                steps[index] <= plan_result.state.reasoning_cutoff or not accepts_replayed_reasoning(part, provider)
            ):
                continue
            new_part = _reduce_part(part, plan_result, lookup)
            reduced += new_part != part
            parts.append(new_part)
        if removed and not parts:
            continue
        rewritten = message if parts == list(message.parts) else dataclasses.replace(message, parts=parts)
        if with_positions:
            rewritten = replace(rewritten, metadata={**(rewritten.metadata or {}), 'sigil_view_position': index})
        result.append(rewritten)
    return result, reduced


def _reduce_part(part: Any, planned: CompactionPlan, lookup: PolicyLookup | None) -> Any:
    if not isinstance(part, (ToolCallPart, ToolReturnPart)):
        return part
    level = planned.level_of(part.tool_call_id or '')
    if level is ToolLOD.FULL:
        return part
    declaration = _policy_for(part.tool_name, lookup)
    if isinstance(part, ToolCallPart):
        args = _args_dict(part)
        if args is None:
            return part
        reduced = declaration.compact_input(args, level)
        return part if encoded(reduced) == encoded(args) else replace(part, args=reduced)
    # Recovery errors are plain text, even for normally structured own tools.
    if part.outcome != 'success' and isinstance(part.content, str):
        from src.engine.tooling.lod import shorten

        return replace(part, content=shorten(part.content, limit_for(level)))
    if contains_binary(part.content) and declaration is GENERIC_LOD:
        return part
    reduced = declaration.compact_output(part.content, level)
    return part if encoded(reduced) == encoded(part.content) else replace(part, content=reduced)


def exceptions(
    messages: Sequence[ModelMessage], planned: CompactionPlan, lookup: PolicyLookup | None
) -> tuple[str, ...]:
    """Content-free reasons a value could not meet its requested representation."""
    ages = tuple(planned.effective_policy.get('lod_ages', DEFAULT_POLICY.lod_ages))
    call_steps = tool_call_steps(messages)
    current = step_count(messages)

    def eligible(call_id: str) -> ToolLOD:
        age = current - call_steps.get(call_id, current)
        return max(planned.level_of(call_id), ToolLOD(sum(age >= fence for fence in ages)))

    reasons: set[str] = set()
    for message in messages:
        for part in message.parts:
            if isinstance(part, (NativeToolCallPart, NativeToolReturnPart)):
                if eligible(part.tool_call_id) is not ToolLOD.FULL:
                    reasons.add('provider_native_exchange')
                continue
            if not isinstance(part, (ToolCallPart, ToolReturnPart)):
                continue
            level = eligible(part.tool_call_id or '')
            if level is ToolLOD.FULL:
                continue
            value = _args_dict(part) if isinstance(part, ToolCallPart) else part.content
            if value is None:
                reasons.add('malformed_arguments')
            elif contains_binary(value) and _policy_for(part.tool_name, lookup) is GENERIC_LOD:
                reasons.add('opaque_binary_or_multimodal')
            elif _policy_for(part.tool_name, lookup) is GENERIC_LOD:
                compact = GENERIC_LOD.compact_input if isinstance(part, ToolCallPart) else GENERIC_LOD.compact_output
                value = compact(value, level)
                size = len(value) if isinstance(value, str) else len(encoded(value))
                if size > limit_for(level):
                    reasons.add('indispensable_generic_metadata')
    return tuple(sorted(reasons))


def _policy_for(tool_name: str, lookup: PolicyLookup | None) -> ToolLODPolicy:
    return (lookup(tool_name) if lookup is not None else None) or GENERIC_LOD


def _args_dict(part: ToolCallPart) -> dict | None:
    import json

    if part.args is None:
        return {}
    if isinstance(part.args, dict):
        return part.args
    try:
        parsed = json.loads(part.args)
    except ValueError, TypeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def part_key(message: ModelMessage, part: Any, index: int) -> str:
    """Stable content-free handles survive merged request envelopes on resume."""
    if isinstance(part, (ToolCallPart, ToolReturnPart)):
        return f'{part.part_kind}:{part.tool_call_id}'
    timestamp = getattr(part, 'timestamp', None) or getattr(message, 'timestamp', None)
    return f'{getattr(part, "part_kind", "part")}:{timestamp}:{index if not isinstance(part, UserPromptPart) else 0}'


def _tool_frontiers(
    messages: Sequence[ModelMessage],
    current: int,
    state: CompactionState,
    policy: CompactionPolicy,
    lookup: PolicyLookup | None,
) -> tuple[int, int, int]:
    """Advance oldest eligible calls first until pending savings reach the target."""
    maximum = tuple(max(old, current - age) for old, age in zip(state.lod_cutoffs, policy.lod_ages))
    if policy.tool_target_chars == 0:
        return maximum
    candidate = state
    if pending_tool_chars(messages, candidate, current, policy, lookup) <= policy.tool_target_chars:
        return state.lod_cutoffs
    for step in sorted(set(tool_call_steps(messages).values())):
        frontiers = tuple(max(old, min(limit, step)) for old, limit in zip(state.lod_cutoffs, maximum))
        candidate = replace(state, lod_cutoffs=frontiers)
        if pending_tool_chars(messages, candidate, current, policy, lookup) <= policy.tool_target_chars:
            break
    return candidate.lod_cutoffs
