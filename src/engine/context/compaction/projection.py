"""Turning the transcript into the request the provider receives.

The projection has three parts, and they can coexist:

1. a model-written checkpoint that replaces everything before its boundary;
2. the exchanges after it, with old reasoning removed and old tool payloads reduced;
3. the protected recent tail, which is never rewritten.

Only two things change the view: reaching the trigger, and applying a summary the caller
obtained. Nothing here drops a turn to make a request fit — that is what the safety
budget and an explicit failure are for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from pydantic_ai.messages import ModelMessage, ThinkingPart, ToolCallPart, ToolReturnPart

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.clock import step_count
from src.engine.context.compaction.coordinator import (
    CompactionPlan,
    PolicyLookup,
    apply,
    exceptions,
    plan,
)
from src.engine.context.compaction.events import CompactionEvent, CompactionReason
from src.engine.context.compaction.maintenance import last_summary_failure
from src.engine.context.compaction.measurements import ProjectionMeasurements
from src.engine.context.compaction.policy import DEFAULT_POLICY, CompactionPolicy
from src.engine.context.compaction.records import last_projection_error
from src.engine.context.compaction.summary import Summary, SummaryRequest, summary_request
from src.engine.context.strategy.rewriting import apply_image_removals, image_removals
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_tokens, part_tokens


def prepare(
    messages: Sequence[ModelMessage],
    budget: ContextBudget,
    *,
    policy: CompactionPolicy = DEFAULT_POLICY,
    lookup: PolicyLookup | None = None,
    provider: str | None = None,
    image_turns: int,
    image_percent: int,
    summary: Summary | None = None,
    compact_opportunistically: bool = False,
    measurements: ProjectionMeasurements | None = None,
) -> ViewResult:
    """Project the history, compacting under pressure or at an instruction boundary."""
    measurements = measurements if measurements is not None else ProjectionMeasurements()
    planned = plan(messages, policy=policy, lookup=lookup, provider=provider)
    working, reduced = _project(messages, planned, lookup, provider)
    working, images = apply_image_removals(working, dict(planned.state.image_removals))
    # The trigger is measured on the request as it would be sent without intervention.
    pressured = _over(budget, working, policy.trigger_percent, measurements) or _exceeds_budget(
        budget, working, measurements
    )
    if pressured or compact_opportunistically or summary is not None:
        planned = _planned(
            messages, policy, lookup, provider, pressure=pressured or compact_opportunistically, summary=summary
        )
        triggers = tuple(
            name
            for enabled, name in ((pressured, 'pressure'), (compact_opportunistically, 'instruction change'))
            if enabled
        )
        planned = replace(
            planned,
            events=(
                *planned.events[: planned.recorded_events],
                *(replace(event, triggers=triggers) for event in planned.events[planned.recorded_events :]),
            ),
        )
        working, reduced = _project(messages, planned, lookup, provider)
        working, images = apply_image_removals(working, dict(planned.state.image_removals))
    if pressured or budget.fits(measurements.tokens(working)) is False:
        from src.engine.context.strategy.constants import image_token_cap

        removals = image_removals(
            working, turn_window=image_turns, max_tokens=image_token_cap(budget.window, image_percent)
        )
        if removals:
            state = replace(
                planned.state, image_removals=tuple({**dict(planned.state.image_removals), **removals}.items())
            )
            planned = replace(planned, state=state)
            working, newly_dropped = apply_image_removals(working, removals)
            images += newly_dropped
            planned = replace(
                planned,
                events=(
                    *planned.events,
                    replace(_event(messages, working, planned, CompactionReason.DETERMINISTIC), triggers=('pressure',)),
                ),
            )

    request: SummaryRequest | None = None
    if pressured and summary is None and _over(budget, working, policy.target_percent, measurements):
        request = summary_request(
            messages,
            boundary=planned.state.summary_boundary,
            previous=planned.state.summary,
            reasoning_blocks=policy.recent_reasoning_blocks,
            tool_calls=policy.recent_tool_calls,
        )
        if request is not None:
            eligible = replace(planned, state=replace(planned.state, summary=None, summary_boundary=None))
            payload, _ = _project(request.messages, eligible, lookup, provider)
            payload, _ = apply_image_removals(payload, dict(planned.state.image_removals))
            request = replace(request, messages=tuple(payload))
        else:
            planned = replace(planned, exceptions=(*planned.exceptions, 'summary_boundary_unavailable'))
    return ViewResult(
        working,
        measurements.tokens(working),
        compacted_parts=reduced,
        dropped_images=images,
        events=planned.events,
        plan=planned,
        summary_request=request,
        summary_error=None
        if summary is not None
        else last_summary_failure(messages) or last_projection_error(messages),
    )


def finish(
    original: Sequence[ModelMessage],
    prepared: ViewResult,
    budget: ContextBudget,
    *,
    policy: CompactionPolicy = DEFAULT_POLICY,
    lookup: PolicyLookup | None = None,
    measurements: ProjectionMeasurements | None = None,
) -> ViewResult:
    """Record the decision and report honestly when the projection does not fit."""
    measurements = measurements if measurements is not None else ProjectionMeasurements()
    planned = prepared.plan
    if planned is None:
        raise ValueError('Shared projection requires a compaction plan')
    working = prepared.messages
    events = list(prepared.events)
    reasons = {*planned.exceptions, *exceptions(original, planned, lookup)}
    if prepared.summary_request is not None:
        reasons.add('summary_pending')
    elif planned.state.summary and _over(budget, working, policy.target_percent, measurements):
        reasons.add('summary_insufficient')
    if budget.window is None:
        reasons.add('window_unknown')
    exceeded = _exceeds_budget(budget, working, measurements)
    if exceeded:
        reasons.add('window')
        events.append(_event(original, working, planned, CompactionReason.EMERGENCY))
    state = replace(planned.state, evaluated_steps=step_count(original))
    projected = [
        replace(event, policy=planned.effective_policy, window=budget.window)
        for event in events[planned.recorded_events :]
    ]
    events = [*events[: planned.recorded_events], *_coalesce(projected)]
    planned = replace(planned, state=state, events=tuple(events), exceptions=tuple(sorted(reasons)))
    diagnostics = metrics(original, working, planned, policy, budget, measurements=measurements)
    return replace(
        prepared,
        messages=working,
        estimated_tokens=measurements.tokens(working),
        dropped_thinking=_thinking_count(original) - _thinking_count(working),
        events=tuple(events),
        plan=planned,
        diagnostics=diagnostics,
        budget_exceeded=exceeded,
    )


def _planned(
    messages: Sequence[ModelMessage],
    policy: CompactionPolicy,
    lookup: PolicyLookup | None,
    provider: str | None,
    *,
    pressure: bool,
    summary: Summary | None,
) -> CompactionPlan:
    """The decision for this projection, with the checkpoint the caller obtained, if any."""
    planned = plan(messages, policy=policy, lookup=lookup, pressure=pressure, provider=provider)
    if summary is None:
        return planned
    planned = replace(planned, state=replace(planned.state, summary=summary.text, summary_boundary=summary.boundary))
    working, _ = _project(messages, planned, lookup, provider)
    return replace(planned, events=(*planned.events, _event(messages, working, planned, CompactionReason.SUMMARY)))


def _project(
    messages: Sequence[ModelMessage],
    planned: CompactionPlan,
    lookup: PolicyLookup | None,
    provider: str | None,
) -> tuple[list[ModelMessage], int]:
    return apply(list(messages), planned, lookup=lookup, provider=provider)


def _over(
    budget: ContextBudget, working: Sequence[ModelMessage], percent: int, measurements: ProjectionMeasurements
) -> bool:
    """Whether instructions, schemas and history together cross a share of the window."""
    if budget.window is None:
        return False
    return measurements.input(working, budget).total * 100 > budget.window * percent


def _exceeds_budget(
    budget: ContextBudget, working: Sequence[ModelMessage], measurements: ProjectionMeasurements
) -> bool:
    return budget.fits(max(0, measurements.input(working, budget).total - budget.fixed)) is False


def _thinking_count(messages: Sequence[ModelMessage]) -> int:
    return sum(isinstance(p, ThinkingPart) for m in messages for p in m.parts)


def _event(
    original: Sequence[ModelMessage],
    working: Sequence[ModelMessage],
    planned: CompactionPlan,
    reason: CompactionReason,
) -> CompactionEvent:
    kept = sum(isinstance(p, ThinkingPart) for m in working for p in m.parts)
    return CompactionEvent(
        step_count(original),
        reason=reason,
        reasoning_kept=kept,
        compacted_calls=len(planned.state.compacted_calls),
        summary_chars=len(planned.state.summary or ''),
        detail={
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
    *,
    measurements: ProjectionMeasurements | None = None,
) -> dict[str, object]:
    """No argument values, tool names, ids or conversation text enter diagnostics."""
    measurements = measurements if measurements is not None else ProjectionMeasurements()
    reasoning = [p for m in working for p in m.parts if isinstance(p, ThinkingPart)]
    unknown = sum(bool(p.signature) and not p.content for p in reasoning)
    estimate = measurements.input(working, budget)
    before_args = sum(part_tokens(p) for m in original for p in m.parts if isinstance(p, ToolCallPart))
    after_args = sum(part_tokens(p) for m in working for p in m.parts if isinstance(p, ToolCallPart))
    before_output = sum(part_tokens(p) for m in original for p in m.parts if isinstance(p, ToolReturnPart))
    after_output = sum(part_tokens(p) for m in working for p in m.parts if isinstance(p, ToolReturnPart))
    return {
        'reasoning_blocks': len(reasoning),
        'reasoning_known_tokens': sum(estimate_tokens(p.content or '') for p in reasoning),
        'reasoning_unknown_blocks': unknown,
        'reasoning_size': 'partial' if unknown else 'estimated',
        'compacted_calls': len(planned.state.compacted_calls),
        'dropped_parts': len(planned.state.dropped_parts),
        'summary_chars': len(planned.state.summary or ''),
        'input_reduction_tokens': max(0, before_args - after_args),
        'output_reduction_tokens': max(0, before_output - after_output),
        'trigger_percent': policy.trigger_percent,
        'target_percent': policy.target_percent,
        'exceptions': planned.exceptions,
        'window': budget.window,
        'projected_input_tokens': estimate.total,
        'input_estimate_source': estimate.source,
        'safety_reserve': budget.safety_reserve,
        'opaque_reasoning_reserve': estimate.opaque_reserve,
        'events': 'recorded plus projected; legacy prefix reconstructed',
    }


def _coalesce(events: Sequence[CompactionEvent]) -> list[CompactionEvent]:
    """A prepared request records one event per boundary, with its strongest reason."""
    severity = {
        CompactionReason.DETERMINISTIC: 0,
        CompactionReason.SUMMARY: 1,
        CompactionReason.EMERGENCY: 2,
    }
    merged: dict[int, CompactionEvent] = {}
    for event in events:
        previous = merged.get(event.step)
        if previous is None:
            merged[event.step] = event
            continue
        merged[event.step] = replace(
            event,
            reason=max((previous.reason, event.reason), key=severity.__getitem__),
            compacted_calls=max(previous.compacted_calls, event.compacted_calls),
            summary_chars=max(previous.summary_chars, event.summary_chars),
            unmet=tuple(sorted({*previous.unmet, *event.unmet})),
            detail={**previous.detail, **event.detail},
            triggers=tuple(dict.fromkeys((*previous.triggers, *event.triggers))),
        )
    return list(merged.values())


__all__ = ['finish', 'metrics', 'prepare']
