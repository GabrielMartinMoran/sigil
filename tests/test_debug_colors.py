"""Debug visuals retain numeric meaning, request identity, and privacy."""

from dataclasses import replace

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.usage import RequestUsage

from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.debug import build_debug_report
from src.engine.context.meter.request import snapshot_request
from src.ui.widgets import debug_view as ui
from tests.ui_helpers import render_text


def report():
    messages = [
        ModelRequest(parts=[UserPromptPart('PRIVATE')]),
        ModelResponse(parts=[TextPart('PRIVATE')], usage=RequestUsage(input_tokens=100, cache_read_tokens=90)),
        ModelResponse(parts=[TextPart('PRIVATE')], usage=RequestUsage(input_tokens=200, cache_read_tokens=30)),
    ]
    meter = ContextMeter(window=1000)
    return build_debug_report(messages, snapshot=meter.snapshot(messages), request=snapshot_request(None))


@pytest.mark.parametrize(
    ('ratio', 'cache', 'context'),
    [(0.2, 'red', 'green'), (0.7, 'yellow', 'yellow'), (0.95, 'green', 'red'), (None, 'dim', 'dim')],
)
def test_semantic_colors_have_opposite_directions(ratio, cache, context):
    assert ui.cache_style(ratio) == cache
    assert ui.context_style(ratio) == context


def test_prompt_cache_is_newest_first_without_changing_request_numbers():
    table = ui._cache_table(report()).renderables[0]
    assert table.columns[0]._cells == ['2', '1', 'all']
    assert table.columns[5]._cells[0].style == 'red'
    assert table.columns[5]._cells[1].style == 'green'


def test_turn_context_contains_fraction_and_color():
    r = report()
    turn = replace(r.turns[0], last_input=950)
    table = ui._turns_table(replace(r, turns=(turn,)))
    cell = table.columns[1]._cells[0]
    assert '95%' in cell.plain
    assert cell.style == 'red'


def test_last_prompt_records_sent_parts_not_the_projected_next_request():
    messages = [
        ModelRequest(parts=[UserPromptPart('PRIVATE')]),
        ModelResponse(
            parts=[ThinkingPart('PRIVATE'), ToolCallPart('run', args={'cmds': ['PRIVATE']}, tool_call_id='c')]
        ),
        ModelRequest(parts=[ToolReturnPart('run', 'PRIVATE', tool_call_id='c')]),
        ModelResponse(parts=[TextPart('PRIVATE')]),
    ]
    snapshot = snapshot_request(ModelRequestParameters(), messages=messages, preview_chars=0)
    r = replace(report(), request=snapshot)
    table = ui._last_request_table(r)
    text = render_text(table, width=120)
    assert 'Last request' in text
    assert 'PRIVATE' not in text
    assert {'cyan', '#d7afff', 'grey62', 'white'} <= {row.style for row in table.rows if row.style}
    assert [s.kind for s in snapshot.sections] == ['user', 'thinking', 'tool_call', 'tool_return', 'text']
    assert 'Last request' in render_text(ui.build_debug_body(r), width=80)


def test_missing_request_is_not_replaced_with_projected_history():
    assert 'No request captured' in render_text(ui._last_request_table(report()))


def test_dynamic_rows_are_orange_and_unknown_reasoning_is_not_zero():
    from types import SimpleNamespace

    params = SimpleNamespace(
        instruction_parts=[SimpleNamespace(name='dynamic-state', dynamic=True, content='PRIVATE')],
        function_tools=[],
        native_tools=['native'],
    )
    snapshot = snapshot_request(
        params, preview_chars=0, messages=[ModelResponse(parts=[ThinkingPart('', signature='opaque')])]
    )
    table = ui._last_request_table(replace(report(), request=snapshot))
    assert table.rows[0].style == '#ffaf5f'
    assert snapshot.instructions[0].preview == ''
    assert snapshot.sections[0].tokens is None
    text = render_text(table, width=120)
    assert 'Known total' in text
    assert 'PRIVATE' not in text


def test_unknown_and_native_parts_have_a_row_instead_of_silently_disappearing():
    from pydantic_ai.messages import NativeToolCallPart

    snapshot = snapshot_request(
        ModelRequestParameters(),
        messages=[
            ModelResponse(parts=[NativeToolCallPart('web_search', args={'PRIVATE': 'PRIVATE'}, tool_call_id='c')])
        ],
    )
    assert snapshot.sections[0].kind == 'tool_call'
    assert snapshot.sections[0].tokens is None
    assert 'PRIVATE' not in repr(snapshot)


def test_real_agent_snapshot_excludes_the_response_it_has_not_received_yet():
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from src.engine.context.capability import ContextViewCapability
    from src.engine.context.strategy.append_only import AppendOnlyStrategy

    async def run():
        cap = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter(window=100000))
        agent = Agent(TestModel(custom_output_text='PRIVATE'), capabilities=[cap])
        first = await agent.run('PRIVATE')
        first_snapshot = cap.last_request
        assert [s.kind for s in first_snapshot.sections] == ['user']
        await agent.run('PRIVATE', message_history=first.all_messages())
        assert [s.kind for s in cap.last_request.sections] == ['user', 'text', 'user']
        assert first_snapshot.sections == first_snapshot.sections[:1]

    asyncio.run(run())


@pytest.mark.parametrize(
    ('ratio', 'cache', 'context'),
    [(0.49, 'red', 'green'), (0.5, 'yellow', 'green'), (0.8, 'green', 'yellow'), (0.9, 'green', 'red')],
)
def test_threshold_boundaries_are_inclusive(ratio, cache, context):
    assert ui.cache_style(ratio) == cache
    assert ui.context_style(ratio) == context


def test_display_thresholds_are_configurable(monkeypatch):
    from src.ui.widgets import debug_styles

    monkeypatch.setattr(debug_styles, 'CACHE_HIT_GOOD_RATIO', 0.6)
    monkeypatch.setattr(debug_styles, 'CONTEXT_DANGER_RATIO', 0.6)
    assert ui.cache_style(0.65) == 'green'
    assert ui.context_style(0.65) == 'red'


def test_plain_text_cache_uses_the_same_descending_order():
    from src.engine.context.meter.debug_render import render_debug_report

    text = render_debug_report(report())
    assert text.index('    2 |') < text.index('    1 |')


def test_timeline_shows_newest_step_first_with_colors():
    messages = [
        ModelRequest(parts=[UserPromptPart('earlier')]),
        ModelResponse(parts=[TextPart('old')], usage=RequestUsage(input_tokens=999)),
        ModelRequest(parts=[UserPromptPart('new')]),
        ModelResponse(parts=[TextPart('private')], usage=RequestUsage(input_tokens=200, cache_read_tokens=180)),
        ModelResponse(parts=[TextPart('private')], usage=RequestUsage(input_tokens=950, cache_read_tokens=95)),
    ]
    meter = ContextMeter(window=1000)
    r = build_debug_report(messages, snapshot=meter.snapshot(messages), request=snapshot_request(None))
    timeline = ui._turn_timeline(r)
    assert timeline.plain.index('Step 2') < timeline.plain.index('Step 1')
    assert '20%' in timeline.plain and '200 / 1,000' in timeline.plain
    assert '90%' in timeline.plain and '95%' in timeline.plain
    assert '999' not in timeline.plain and 'private' not in timeline.plain
    assert '┌' in timeline.plain and '└' in timeline.plain
    assert {'green', 'red'} <= {span.style for span in timeline.spans}


def test_timeline_preserves_unmeasured_steps_and_previous_turn_while_waiting():
    messages = [
        ModelRequest(parts=[UserPromptPart('first')]),
        ModelResponse(parts=[TextPart('one')], usage=RequestUsage(input_tokens=100, cache_read_tokens=50)),
        ModelResponse(parts=[TextPart('two')]),
        ModelRequest(parts=[UserPromptPart('next')]),
    ]
    meter = ContextMeter(window=1000)
    r = build_debug_report(messages, snapshot=meter.snapshot(messages), request=snapshot_request(None))
    text = ui._turn_timeline(r).plain
    assert 'Step 1' in text and 'Step 2' in text
    assert '?' in text
    assert 'Turn 1' in text


def test_timeline_marks_compaction_at_its_step():
    from src.engine.context.compaction.events import CompactionEvent, CompactionReason

    messages = [
        ModelRequest(parts=[UserPromptPart('first')]),
        ModelResponse(parts=[TextPart('old')], usage=RequestUsage(input_tokens=100)),
        ModelRequest(parts=[UserPromptPart('second')]),
        ModelResponse(parts=[TextPart('one')], usage=RequestUsage(input_tokens=300)),
        ModelResponse(parts=[TextPart('two')], usage=RequestUsage(input_tokens=400)),
    ]
    meter = ContextMeter(window=1000)
    r = build_debug_report(
        messages,
        snapshot=meter.snapshot(messages),
        request=snapshot_request(None),
        events=[CompactionEvent(step=3, reason=CompactionReason.EMERGENCY)],
    )
    text = ui._turn_timeline(r).plain
    assert 'compaction' in text.lower() and 'emergency' in text.lower()
    assert text.index('Step 2') < text.index('emergency')


def test_timeline_handles_missing_window_and_invalid_cache_counts():
    messages = [
        ModelRequest(parts=[UserPromptPart('test')]),
        ModelResponse(parts=[TextPart('result')], usage=RequestUsage(input_tokens=100, cache_read_tokens=150)),
    ]
    meter = ContextMeter(window=None)
    r = build_debug_report(messages, snapshot=meter.snapshot(messages), request=snapshot_request(None))
    timeline = ui._turn_timeline(r)
    assert '100 / ? (?)' in timeline.plain
    assert 'cache ?' in timeline.plain
    assert {span.style for span in timeline.spans} >= {'dim'}


def test_timeline_with_no_steps_has_empty_state():
    meter = ContextMeter(window=1000)
    r = build_debug_report([], snapshot=meter.snapshot([]), request=snapshot_request(None))
    assert 'No completed steps yet' in ui._turn_timeline(r).plain


def test_timeline_aligns_columns_with_different_step_and_metric_widths():
    messages = [ModelRequest(parts=[UserPromptPart('test')])]
    messages.extend(
        ModelResponse(parts=[TextPart('result')], usage=RequestUsage(input_tokens=value, cache_read_tokens=0))
        for value in [1, 100, 900, 2, 3, 4, 5, 6, 7, 1000]
    )
    meter = ContextMeter(window=10000)
    r = build_debug_report(messages, snapshot=meter.snapshot(messages), request=snapshot_request(None))
    lines = ui._turn_timeline(r).plain.splitlines()[1:]
    assert len(lines) == 10
    assert 'Step 10' in lines[0] and 'Step 1 ' in lines[-1]
    assert len({line.index('context') for line in lines}) == 1
    assert len({line.index('cache') for line in lines}) == 1
