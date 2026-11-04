"""Tests for the debug view: prompt cache, request snapshot and report."""

from __future__ import annotations

from pydantic_ai.messages import (
    CompactionPart,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

from src.engine.context.meter.cache import cache_summary, request_cache_rows
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.debug import build_debug_report, debug_messages
from src.engine.context.meter.debug_render import render_debug_report
from src.engine.context.meter.request import snapshot_request
from src.engine.context.strategy.view_result import ViewResult

# --- helpers ---------------------------------------------------------------


def _response(*, text: str = 'ok', **usage) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)], usage=RequestUsage(**usage))


def _request(*parts) -> ModelRequest:
    return ModelRequest(parts=list(parts))


class _Part:
    """Stand-in de `InstructionPart` / `ToolDefinition` para el snapshot."""

    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _Params:
    def __init__(self, *, instruction_parts=None, function_tools=None, native_tools=None) -> None:
        self.instruction_parts = instruction_parts
        self.function_tools = function_tools
        self.native_tools = native_tools


# --- cache -----------------------------------------------------------------


def test_cache_rows_split_cached_from_uncached():
    """`input_tokens` is inclusive: the cached ones are already inside."""
    messages = [
        _request(UserPromptPart(content='hello')),
        _response(input_tokens=1_000, cache_read_tokens=800, cache_write_tokens=100, output_tokens=50),
    ]
    (row,) = request_cache_rows(messages)

    assert row.index == 1
    assert row.input_tokens == 1_000
    assert row.cached == 900
    assert row.uncached == 100
    assert row.hit_ratio == 0.9


def test_cache_summary_aggregates_requests():
    messages = [
        _response(input_tokens=1_000, cache_read_tokens=0, cache_write_tokens=1_000, output_tokens=10),
        _response(input_tokens=1_000, cache_read_tokens=900, cache_write_tokens=0, output_tokens=10),
    ]
    summary = cache_summary(messages)

    assert summary.requests == 2
    assert summary.input_tokens == 2_000
    assert summary.cache_write == 1_000
    assert summary.cache_read == 900
    assert summary.cached == 1_900
    assert summary.uncached == 100
    assert summary.hit_ratio == 0.95


def test_cache_summary_without_usage_is_empty():
    summary = cache_summary([_request(UserPromptPart(content='hello'))])
    assert not summary.has_data
    assert summary.rows == ()
    assert summary.hit_ratio == 0.0


def test_cache_summary_skips_responses_without_usage():
    """A response without usage is not a measured request: it does not count as 0 tokens."""
    messages = [ModelResponse(parts=[TextPart(content='x')]), _response(input_tokens=10, output_tokens=1)]
    assert len(request_cache_rows(messages)) == 1
    assert cache_summary(messages).unmeasured == 1


def test_cache_summary_skips_unmeasured_requests():
    """`unmeasured_requests` marks omitted usage: its totals are not a measurement."""
    messages = [_response(input_tokens=0, output_tokens=0, unmeasured_requests=1)]
    summary = cache_summary(messages)
    assert not summary.has_data
    assert summary.unmeasured == 1


def test_cache_summary_adds_cost_when_reported():
    from decimal import Decimal

    messages = [
        _response(input_tokens=100, output_tokens=10, cost=Decimal('0.0010')),
        _response(input_tokens=100, output_tokens=10, cost=Decimal('0.0020')),
    ]
    summary = cache_summary(messages)
    assert summary.cost is not None
    assert round(summary.cost, 6) == 0.003


def test_cache_cost_is_none_when_provider_does_not_report_it():
    summary = cache_summary([_response(input_tokens=100, output_tokens=10)])
    assert summary.cost is None


# --- request snapshot ------------------------------------------------------


def test_snapshot_request_reads_instructions_and_tools():
    params = _Params(
        instruction_parts=[
            _Part(name=None, dynamic=False, content='prompt base'),
            _Part(name='workspace', dynamic=True, content='AGENTS.md content'),
        ],
        function_tools=[
            _Part(
                name='read_file',
                description='Read a file',
                parameters_json_schema={'type': 'object', 'properties': {}},
            )
        ],
        native_tools=[object()],
    )
    snapshot = snapshot_request(params)

    assert [block.name for block in snapshot.instructions] == ['(base)', 'workspace']
    assert snapshot.dynamic_instructions[0].name == 'workspace'
    assert snapshot.instructions_tokens > 0
    assert [tool.name for tool in snapshot.tools] == ['read_file']
    assert snapshot.tools_tokens > 0
    assert snapshot.native_tools == 1
    assert snapshot.captured is True


def test_snapshot_request_tolerates_missing_parameters():
    snapshot = snapshot_request(None)
    assert snapshot.instructions == ()
    assert snapshot.tools == ()
    assert snapshot.instructions_tokens == 0
    assert snapshot.tools_tokens == 0
    # Without `ModelRequestParameters` there was no request: the zeros are not a measurement.
    assert snapshot.captured is False


def test_snapshot_marks_deferred_tools():
    params = _Params(
        function_tools=[_Part(name='hidden', description='', parameters_json_schema={}, defer_loading=True)]
    )
    assert snapshot_request(params).tools[0].deferred is True


def test_snapshot_preview_is_clipped():
    params = _Params(instruction_parts=[_Part(name='big', dynamic=False, content='x' * 500)])
    block = snapshot_request(params, preview_chars=40).instructions[0]
    assert len(block.preview) <= 40
    assert block.tokens == 125


# --- debug messages --------------------------------------------------------


def test_debug_messages_label_parts_and_count_tokens():
    messages = [
        _request(UserPromptPart(content='hello world')),
        ModelResponse(parts=[ThinkingPart(content='thinking'), TextPart(content='done')]),
        _request(ToolReturnPart(tool_name='read_file', content='content', tool_call_id='1')),
    ]
    rows = debug_messages(list(messages))

    assert [row.role for row in rows] == ['request', 'response', 'request']
    assert rows[0].kinds == 'user'
    assert rows[1].kinds == 'thinking · text'
    assert rows[2].kinds == 'tool_return'
    assert rows[2].parts[0].label == 'read_file → ok'
    assert all(row.tokens > 0 for row in rows)


def test_debug_messages_clip_long_previews_but_keep_the_real_cost():
    big = 'a' * 5_000
    rows = debug_messages(
        [_request(ToolReturnPart(tool_name='read_file', content=big, tool_call_id='1'))], part_chars=100
    )

    part = rows[0].parts[0]
    assert len(part.preview) == 100
    assert part.clipped is True
    assert part.clipped_chars == 4_900
    # The cost is measured on the whole text, not on the preview.
    assert part.tokens == 1_250


def test_debug_messages_reports_compaction_and_instructions():
    messages = [ModelRequest(parts=[CompactionPart(content='summary of old turns')], instructions='base prompt')]
    row = debug_messages(messages)[0]
    assert row.parts[0].kind == 'compaction'
    assert row.instructions == ('(inline)',)


def test_debug_messages_handle_tool_call_args():
    rows = debug_messages([ModelResponse(parts=[ToolCallPart(tool_name='run', args={'cmd': 'ls'})])])
    assert rows[0].kinds == 'tool_call'
    assert 'ls' in rows[0].parts[0].preview


# --- report ----------------------------------------------------------------


def _session() -> list:
    return [
        _request(UserPromptPart(content='hello')),
        _response(input_tokens=500, cache_read_tokens=400, cache_write_tokens=0, output_tokens=20),
    ]


def _report(messages, *, view_result=None):
    meter = ContextMeter(window=10_000, output_reserve=1_000)
    meter.set_overhead(instructions=200, tools=800)
    return build_debug_report(
        messages,
        snapshot=meter.snapshot(messages),
        request=snapshot_request(_Params(instruction_parts=[_Part(name='base', dynamic=False, content='x' * 40)])),
        view_result=view_result,
        effort='high',
        model='test/model',
    )


def test_report_measures_log_against_view():
    messages = _session()
    dropped = ViewResult(messages=[messages[0]], estimated_tokens=5, dropped_messages=1)
    report = _report(messages, view_result=dropped)

    assert report.model == 'test/model'
    assert report.effort == 'high'
    assert report.log_tokens == sum(row.tokens for row in report.log)
    assert len(report.view) == 1
    assert report.dropped_tokens > 0
    assert report.dropped_tokens == report.log_tokens - report.view_tokens
    assert report.reduction_percent is not None
    assert 'dropped messages: 1' in report.strategies_note


def test_report_without_view_sends_everything():
    report = _report(_session())
    assert not report.has_view
    assert report.view == ()
    assert report.view_tokens == report.log_tokens
    assert report.dropped_tokens == 0
    assert report.strategies_note == ''


def test_report_carries_cache_and_fixed_cost():
    report = _report(_session())
    assert report.cache.requests == 1
    assert report.cache.cached == 400
    assert report.request.instructions_tokens > 0
    assert report.snapshot.budget.tools == 800


# --- render ----------------------------------------------------------------


def test_render_debug_report_includes_every_section():
    messages = _session()
    dropped = ViewResult(messages=[messages[0]], estimated_tokens=5, dropped_thinking=2)
    text = render_debug_report(_report(messages, view_result=dropped))

    assert 'Agent debug' in text
    assert 'Context (next request)' in text
    assert 'Fixed cost per request' in text
    assert 'Instruction blocks' in text
    assert 'Prompt cache' in text
    assert 'Per request' in text
    assert 'uncached' in text
    assert 'Applied view' in text
    assert 'Agent view' in text
    assert 'Session log' in text


def test_render_debug_report_without_usage_says_so():
    text = render_debug_report(_report([_request(UserPromptPart(content='solo yo'))]))
    assert '(no usage reported yet)' in text
