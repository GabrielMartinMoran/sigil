"""Read and explicitly migrate durable projection records, without planning new edits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from pydantic_ai.messages import ModelMessage, ThinkingPart

from src.engine.context.compaction.clock import step_of_message, tool_call_steps
from src.engine.context.compaction.events import (
    LEGACY_POLICY_VERSION,
    POLICY_VERSION,
    RECORD_KEY,
    CompactionEvent,
    CompactionState,
)
from src.engine.context.compaction.identity import legacy_part_key, part_key


class ContextPolicyVersionError(ValueError):
    """A newer policy record needs an explicit migration, not silent reinterpretation."""


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
        record_anchors = anchors
        if record.get('version') in (2, 3):
            record_anchors = anchors | {legacy_part_key(m, p, i) for m in messages for i, p in enumerate(m.parts)}
        if record.get('anchor') and record['anchor'] not in record_anchors:
            continue
        if record.get('version') not in (POLICY_VERSION, LEGACY_POLICY_VERSION, 3):
            raise ContextPolicyVersionError('Unsupported context policy record; explicit migration is required.')
        request_id = record.get('request_id')
        if request_id and request_id in seen:
            continue
        if request_id:
            seen.add(request_id)
        candidate = _migrated(record, messages)
        if candidate.evaluated_steps > message_steps[index]:
            raise ContextPolicyVersionError('Context frontier points beyond its owning response.')
        state = candidate
        for row in record['events']:
            event = CompactionEvent.from_row(row)
            if event not in events:
                events.append(event)
    return state, tuple(events)


def _migrated(record: Mapping[str, Any], messages: Sequence[ModelMessage]) -> CompactionState:
    """Read a stored decision, expanding the frontiers the previous shape used.

    Version 2 stored step frontiers per level of detail. They are turned into the call
    and part identities the current shape uses. Selection survives, but the current
    compactors can produce different bytes: migration is a deliberate cache reset.
    """
    state = CompactionState.from_row(record['state'])
    if record.get('version') in (2, 3):
        keys: dict[str, list[str]] = {}
        for message in messages:
            for index, part in enumerate(message.parts):
                keys.setdefault(legacy_part_key(message, part, index), []).append(part_key(message, part, index))
        state = replace(
            state,
            dropped_parts=tuple(key for old in state.dropped_parts for key in keys.get(old, [old])),
            summary_boundary=(
                keys.get(state.summary_boundary, [state.summary_boundary])[0] if state.summary_boundary else None
            ),
        )
    if record.get('version') != LEGACY_POLICY_VERSION:
        return state
    legacy = record['state']
    steps = step_of_message(messages)
    cutoff = int(legacy.get('reasoning_cutoff') or 0)
    dropped = set(state.dropped_parts)
    for index, message in enumerate(messages):
        if steps[index] > cutoff:
            continue
        for offset, part in enumerate(message.parts):
            if isinstance(part, ThinkingPart):
                dropped.add(part_key(message, part, offset))
    cutoffs: tuple[int, ...] = tuple(int(fence) for fence in legacy.get('lod_cutoffs') or ())
    compacted = set(state.compacted_calls)
    if cutoffs:
        compacted.update(
            call_id for call_id, step in tool_call_steps(messages).items() if sum(step <= fence for fence in cutoffs)
        )
    return replace(
        state,
        dropped_parts=tuple(sorted(dropped)),
        compacted_calls=tuple(sorted(compacted)),
    )


def last_projection_error(messages: Sequence[ModelMessage]) -> str | None:
    """Last recorded checkpoint failure, including a pre-network budget rejection."""
    for message in reversed(messages):
        record = (message.metadata or {}).get(RECORD_KEY)
        if isinstance(record, dict):
            value = record.get('summary_error')
            return value if isinstance(value, str) else None
    return None
