"""Shared view pipeline: coordinated maintenance, reasoning-first fitting, diagnostics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from pydantic_ai.messages import ModelMessage, ModelResponse, ThinkingPart, ToolCallPart, ToolReturnPart

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.clock import step_count, step_of_message
from src.engine.context.compaction.coordinator import (
    CompactionPlan,
    PolicyLookup,
    apply,
    exceptions,
    part_key,
    pending_tool_chars,
    plan,
)
from src.engine.context.compaction.events import CompactionEvent, CompactionReason
from src.engine.context.compaction.policy import CompactionPolicy
from src.engine.context.strategy.rewriting import drop_stale_images, fit_to_budget, keep_fitting_steps
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_messages_tokens, estimate_tokens, part_tokens
from src.engine.tooling.lod import ToolLOD

POSITION_KEY = 'sigil_view_position'


def prepare(
    messages: Sequence[ModelMessage],
    budget: ContextBudget,
    *,
    policy: CompactionPolicy,
    lookup: PolicyLookup | None,
    provider: str | None,
    image_turns: int,
    image_percent: int,
) -> ViewResult:
    """Image expiry is independent; all text reductions pass through one plan."""
    from src.engine.context.strategy.constants import image_token_cap

    planned = plan(messages, policy=policy, lookup=lookup, window=budget.window, provider=provider)
    working, reduced = apply(list(messages), planned, lookup=lookup, provider=provider, with_positions=True)
    available = budget.available_for_history
    if available is not None and estimate_messages_tokens(working) > available:
        planned = plan(messages, policy=policy, lookup=lookup, pressure=True, window=budget.window, provider=provider)
        working, reduced = apply(list(messages), planned, lookup=lookup, provider=provider, with_positions=True)
    working, images = drop_stale_images(
        working, turn_window=image_turns, max_tokens=image_token_cap(budget.window, available, image_percent)
    )
    return ViewResult(
        working,
        estimate_messages_tokens(working),
        truncated_results=reduced,
        dropped_images=images,
        events=planned.events,
        plan=planned,
    )


def finish(
    original: Sequence[ModelMessage],
    prepared: ViewResult,
    budget: ContextBudget,
    *,
    policy: CompactionPolicy,
    lookup: PolicyLookup | None,
    provider: str | None,
    keep_recent: int,
) -> ViewResult:
    """Exhaust reasoning before losing turns; an indivisible overfull tail is explicit."""
    planned = prepared.plan
    if planned is None:
        raise ValueError('Shared projection requires a compaction plan')
    working = prepared.messages
    available = budget.available_for_history
    events = list(prepared.events)
    state = planned.state
    dropped = 0
    if available is not None and estimate_messages_tokens(working) > available:
        fitted = keep_fitting_steps(working, available, provider=provider)
        if fitted is None:
            fitted, _, _ = _without_reasoning(working)
        if fitted != working:
            state = replace(state, reasoning_cutoff=max(state.reasoning_cutoff, _cutoff(original, fitted)))
            events.append(_event(original, fitted, CompactionReason.BUDGET))
        working = fitted
        if estimate_messages_tokens(working) > available:
            from pydantic_ai.messages import CompactionPart

            head = working[:1] if working and any(isinstance(p, CompactionPart) for p in working[0].parts) else []
            tail = working[len(head) :]
            working, dropped = fit_to_budget(
                tail, available, pinned_tools=policy.pinned_tools, keep_recent=keep_recent, keep_head=head
            )
            if estimate_messages_tokens(working) > available:
                working, more = fit_to_budget(
                    working[len(head) :], available, pinned_tools=policy.pinned_tools, keep_head=head
                )
                dropped += more
            events.append(_event(original, working, CompactionReason.EMERGENCY, dropped=dropped))
    exceeded = budget.fits(estimate_messages_tokens(working)) is False
    if exceeded and (not events or events[-1].reason is not CompactionReason.EMERGENCY):
        events.append(_event(original, working, CompactionReason.EMERGENCY))
    if exceeded:
        events[-1] = replace(events[-1], unmet=tuple(sorted({*events[-1].unmet, 'window'})))
    if dropped:
        retained = {(message.metadata or {}).get(POSITION_KEY) for message in working}
        dropped_parts = set(state.dropped_parts)
        for message in prepared.messages:
            index = (message.metadata or {}).get(POSITION_KEY)
            if index is not None and index not in retained:
                dropped_parts.update(
                    part_key(original[index], part, offset) for offset, part in enumerate(original[index].parts)
                )
        state = replace(state, dropped_parts=tuple(sorted(dropped_parts)))
    reasons = exceptions(original, planned, lookup)
    if reasons and len(events) > planned.recorded_events:
        events[-1] = replace(events[-1], unmet=tuple(sorted({*events[-1].unmet, *reasons})))
    state = replace(state, evaluated_steps=step_count(original))
    projected_events = [
        replace(event, policy=planned.effective_policy, window=budget.window)
        for event in events[planned.recorded_events :]
    ]
    events = [*events[: planned.recorded_events], *_coalesce(projected_events)]
    planned = replace(planned, state=state, events=tuple(events), exceptions=reasons)
    diagnostics = metrics(original, working, planned, policy, budget, lookup)
    cleaned = [
        replace(message, metadata={k: v for k, v in (message.metadata or {}).items() if k != POSITION_KEY} or None)
        for message in working
    ]
    return replace(
        prepared,
        messages=cleaned,
        estimated_tokens=estimate_messages_tokens(cleaned),
        dropped_messages=dropped,
        dropped_thinking=_thinking_count(original) - _thinking_count(cleaned),
        events=tuple(events),
        plan=planned,
        diagnostics=diagnostics,
        budget_exceeded=exceeded,
    )


def _without_reasoning(messages: Sequence[ModelMessage]) -> tuple[list[ModelMessage], int, int]:
    from src.engine.context.strategy.rewriting import drop_thinking

    result, dropped = drop_thinking(list(messages))
    return result, 0, dropped


def _cutoff(original: Sequence[ModelMessage], fitted: Sequence[ModelMessage]) -> int:
    retained = {
        (m.metadata or {}).get(POSITION_KEY) for m in fitted if any(isinstance(p, ThinkingPart) for p in m.parts)
    }
    steps = step_of_message(original)
    return max(
        (
            steps[i]
            for i, m in enumerate(original)
            if any(isinstance(p, ThinkingPart) for p in m.parts) and i not in retained
        ),
        default=0,
    )


def _thinking_count(messages: Sequence[ModelMessage]) -> int:
    return sum(isinstance(p, ThinkingPart) for m in messages for p in m.parts)


def _event(
    original: Sequence[ModelMessage],
    working: Sequence[ModelMessage],
    reason: CompactionReason,
    *,
    dropped: int = 0,
) -> CompactionEvent:
    kept = sum(isinstance(m, ModelResponse) and any(isinstance(p, ThinkingPart) for p in m.parts) for m in working)
    return CompactionEvent(
        step_count(original),
        reason=reason,
        reasoning_kept=kept,
        detail={
            'dropped_messages': dropped,
            'reasoning_after': kept,
            'reasoning_tokens_after': sum(
                estimate_tokens(p.content or '') for m in working for p in m.parts if isinstance(p, ThinkingPart)
            ),
        },
    )


def metrics(
    original: Sequence[ModelMessage],
    working: Sequence[ModelMessage],
    planned: CompactionPlan,
    policy: CompactionPolicy,
    budget: ContextBudget,
    lookup: PolicyLookup | None,
) -> dict[str, object]:
    """No argument values, tool names, ids or conversation text enter diagnostics."""
    reasoning = [p for m in working for p in m.parts if isinstance(p, ThinkingPart)]
    unknown = sum(bool(p.signature) and not p.content for p in reasoning)
    calls = {part.tool_call_id for message in working for part in message.parts if isinstance(part, ToolCallPart)}
    counts = {
        str(int(level)): sum(item is level for call, item in planned.lod.items() if call in calls) for level in ToolLOD
    }
    before_args = sum(part_tokens(p) for m in original for p in m.parts if isinstance(p, ToolCallPart))
    after_args = sum(part_tokens(p) for m in working for p in m.parts if isinstance(p, ToolCallPart))
    before_output = sum(part_tokens(p) for m in original for p in m.parts if isinstance(p, ToolReturnPart))
    after_output = sum(part_tokens(p) for m in working for p in m.parts if isinstance(p, ToolReturnPart))
    return {
        'reasoning_steps': sum(
            isinstance(m, ModelResponse) and any(isinstance(p, ThinkingPart) for p in m.parts) for m in working
        ),
        'reasoning_target_steps': policy.reasoning_steps,
        'reasoning_ceiling_steps': policy.reasoning_trigger_steps,
        'reasoning_target_percent': policy.reasoning_max_percent,
        'reasoning_ceiling_percent': policy.reasoning_trigger_percent,
        'reasoning_known_tokens': sum(estimate_tokens(p.content or '') for p in reasoning),
        'reasoning_unknown_blocks': unknown,
        'reasoning_size': 'partial' if unknown else 'estimated',
        'lod_calls': counts,
        'input_reduction_tokens': max(0, before_args - after_args),
        'output_reduction_tokens': max(0, before_output - after_output),
        'exceptions': planned.exceptions,
        'window': budget.window,
        'events': 'recorded plus projected; legacy prefix reconstructed',
        'tools_pending_chars': pending_tool_chars(original, planned.state, step_count(original), policy, lookup),
        'tools_ceiling_chars': policy.tool_trigger_chars,
        'tools_target_chars': policy.tool_target_chars,
    }


def _coalesce(events: Sequence[CompactionEvent]) -> list[CompactionEvent]:
    """A prepared request records one event per boundary, with its strongest reason."""
    severity = {CompactionReason.OPERATIONAL: 0, CompactionReason.BUDGET: 1, CompactionReason.EMERGENCY: 2}
    merged: dict[int, CompactionEvent] = {}
    for event in events:
        previous = merged.get(event.step)
        if previous is None:
            merged[event.step] = event
            continue
        merged[event.step] = replace(
            event,
            reason=max((previous.reason, event.reason), key=severity.__getitem__),
            max_lod=max(previous.max_lod, event.max_lod),
            unmet=tuple(sorted({*previous.unmet, *event.unmet})),
            detail={**previous.detail, **event.detail},
        )
    return list(merged.values())
