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
    assert output.index('Tool schemas') < output.index('1.1 user-prompt') < output.index('Agent context')
    assert '50 tokens' in output and '? tokens' in output
    assert '╭' in output and '╰' in output


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
    panels = list(stack.renderables)
    assert panels[1].border_style == '#ffaf5f'
    assert panels[2].border_style == 'white'
    assert panels[3].border_style == '#ffaf5f'
