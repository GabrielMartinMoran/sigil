"""The debug stack describes the last captured request without exposing content."""

from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.debug import build_debug_report
from src.engine.context.meter.request import InstructionRow, PromptSection, RequestSnapshot, ToolRow
from src.ui.widgets.debug_view import build_debug_body
from tests.ui_helpers import render_text


def test_stack_shows_ordered_blocks_and_unknown_sizes() -> None:
    request = RequestSnapshot(
        captured=True,
        history_captured=True,
        instructions=(InstructionRow('base', False, 10, ''), InstructionRow('workspace', True, 20, '')),
        tools=(ToolRow('read_file', 30),),
        sections=(PromptSection('user', '1.1 user-prompt', 40), PromptSection('dynamic', 'Agent context', 50)),
        native_tools=1,
    )
    report = build_debug_report([], snapshot=ContextMeter().snapshot([]), request=request)
    output = render_text(build_debug_body(report), width=100)
    assert 'Last request · prompt stack' in output
    assert output.index('base') < output.index('workspace') < output.index('Tool schemas')
    assert output.index('Tool schemas') < output.index('History') < output.index('Agent context')
    assert '1.1 user-prompt' not in output
    assert '50 tokens' in output and '? tokens' in output
    assert '┌' in output and '└' in output
    assert '├' in output and '┬' not in output


def test_stack_colors_dynamic_context_and_leaves_history_white() -> None:
    from src.ui.widgets.debug_view import _prompt_stack

    request = RequestSnapshot(
        captured=True,
        history_captured=True,
        instructions=(InstructionRow('workspace', True, 20, ''),),
        sections=(PromptSection('user', 'history', 40), PromptSection('dynamic', 'Agent context', 50)),
    )
    report = build_debug_report([], snapshot=ContextMeter().snapshot([]), request=request)
    stack = _prompt_stack(report)
    table = list(stack.renderables)[1]
    assert table.rows[0].style == '#ffaf5f'
    assert table.rows[1].style == 'white'
    assert table.rows[2].style == '#ffaf5f'


def test_adjacent_history_parts_are_grouped_without_crossing_dynamic_blocks() -> None:
    request = RequestSnapshot(
        captured=True,
        history_captured=True,
        sections=(
            PromptSection('user', 'user', 12),
            PromptSection('tool_call', 'tool', 18),
            PromptSection('dynamic', 'Agent context', 8),
            PromptSection('thinking', 'opaque', None),
            PromptSection('text', 'answer', 9),
        ),
    )
    report = build_debug_report([], snapshot=ContextMeter().snapshot([]), request=request)
    output = render_text(build_debug_body(report), width=100)
    assert 'History  ·  30 tokens' in output
    assert 'History  ·  ? tokens' in output
    assert output.index('History') < output.index('Agent context') < output.rindex('History')
