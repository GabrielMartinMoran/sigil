"""Operational tables distinguish recorded history, projection and unknown sizes."""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.core.context.meter.context_meter import ContextMeter
from src.core.context.meter.debug import build_debug_report
from src.core.context.meter.debug_render import render_debug_report
from src.core.context.meter.request_snapshot import RequestSnapshot
from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy
from tests.support.context_compaction_records import history, pressured_window, run_recorded


def report(messages: list, *, window: int = 100000):
    meter = ContextMeter(window=window, output_reserve=0)
    view = AppendOnlyStrategy().view(messages, meter.budget())
    return build_debug_report(messages, snapshot=meter.snapshot(messages), request=RequestSnapshot(), view_result=view)


def test_unknown_reasoning_size_and_thresholds_are_visible_without_content() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart(content='PRIVATE text')]),
        ModelResponse(parts=[ThinkingPart(content='', signature='SECRET signature')]),
    ]
    text = render_debug_report(report(messages))
    assert 'unknown signed blocks 1' in text
    assert 'partial' in text
    assert 'trigger 60%; target 40%' in text
    assert '0 + ? (1 unknown)' in text
    assert 'SECRET' not in text and 'PRIVATE' not in text


def test_projected_events_do_not_masquerade_as_observed_turn_history() -> None:
    messages = history(30)
    window = pressured_window(messages)
    projected = report(messages, window=window)
    assert projected.view_result.events
    assert not any(turn.events for turn in projected.turns)
    assert 'projected/reconstructed: step 30' in render_debug_report(projected)
    recorded = report(run_recorded(messages, window=window), window=window)
    assert any(turn.events for turn in recorded.turns)
    assert 'recorded: step 30' in render_debug_report(recorded)


def test_generic_exceptions_are_content_free_and_visible() -> None:
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name='foreign', args={}, tool_call_id='c')]),
        ModelRequest(
            parts=[ToolReturnPart(tool_name='foreign', content={'error': 'PRIVATE' * 1000}, tool_call_id='c')]
        ),
    ]
    messages += history(30)
    view = AppendOnlyStrategy().view(messages, ContextMeter(window=pressured_window(messages)).budget())
    # The large failure is indispensable; do not silently delete it to hit a target.
    assert 'indispensable_generic_metadata' in view.diagnostics['exceptions']
    diagnostics = str(view.diagnostics)
    assert 'PRIVATE' not in diagnostics
