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
    from src.engine.context.strategy.windowed import WindowedStrategy

    async def run():
        cap = ContextViewCapability(strategy=WindowedStrategy(), meter=ContextMeter(window=100000))
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
