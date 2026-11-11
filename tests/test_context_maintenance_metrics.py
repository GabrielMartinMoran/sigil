"""Operational tables distinguish recorded history, projection and unknown sizes."""

from __future__ import annotations

from dataclasses import replace

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.debug import build_debug_report
from src.engine.context.meter.debug_render import render_debug_report
from src.engine.context.meter.request import RequestSnapshot
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from tests.test_context_compaction_records import history, pressured_window, run_recorded


def report(messages: list, *, window: int = 100000):
    meter = ContextMeter(window=window, output_reserve=0)
    view = AppendOnlyStrategy().view(messages, meter.budget())
    return build_debug_report(messages, snapshot=meter.snapshot(messages), request=RequestSnapshot(), view_result=view)


def test_unknown_reasoning_size_and_thresholds_are_visible_without_content():
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


def test_skill_metrics_render_in_text_and_debug_view():
    from src.ui.widgets.debug_view import build_debug_body

    metrics = {
        'instructions_tokens': 100,
        'resources_tokens': 20,
        'resources': 3,
        'partial': 1,
        'binary': 2,
        'unavailable': 3,
        'unavailable_skills': 4,
    }
    debug_report = replace(report(history(20)), request=RequestSnapshot(captured=True, skills=metrics))

    text = render_debug_report(debug_report)
    assert 'instructions ~100 tokens; resources ~20 tokens; resources 3; partial 1' in text
    assert 'binary 2; unavailable 3; unavailable skills 4' in text
    assert build_debug_body(debug_report) is not None


def test_projected_events_do_not_masquerade_as_observed_turn_history():
    messages = history(30)
    window = pressured_window(messages)
    projected = report(messages, window=window)
    assert projected.view_result.events
    assert not any(turn.events for turn in projected.turns)
    assert 'projected/reconstructed: step 30' in render_debug_report(projected)
    recorded = report(run_recorded(messages, window=window), window=window)
    assert any(turn.events for turn in recorded.turns)
    assert 'recorded: step 30' in render_debug_report(recorded)


def test_generic_exceptions_are_content_free_and_visible():
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
