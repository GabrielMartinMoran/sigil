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
    assert output.index('Base Instructions') < output.index('Tool Definitions') < output.index('Project Instructions')
    assert output.index('Tool Definitions') < output.index('Conversation History') < output.index('Agent Context')
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
    assert 'Conversation History  ·  30 tokens' in output
    assert 'Conversation History  ·  ? tokens' in output
    assert output.index('Conversation History') < output.index('Agent Context') < output.rindex('Conversation History')


def test_stack_uses_semantic_order_for_each_known_block() -> None:
    names = ('(base)', 'agent-profile', 'subagents', 'skills', 'workspace', 'loaded-skills')
    request = RequestSnapshot(
        captured=True,
        history_captured=True,
        instructions=tuple(InstructionRow(name, False, 1, '') for name in names),
        tools=(ToolRow('read_file', 1),),
        sections=(PromptSection('user', 'user', 1), PromptSection('dynamic', 'Agent context', 1)),
    )
    report = build_debug_report([], snapshot=ContextMeter().snapshot([]), request=request)
    from src.ui.widgets.debug_view import _prompt_stack

    output = render_text(_prompt_stack(report), width=100)
    labels = (
        'Base Instructions',
        'Agent Profile',
        'Available Agents',
        'Tool Definitions',
        'Available Skills',
        'Project Instructions',
        'Loaded Skills',
        'Conversation History',
        'Agent Context',
    )
    assert [output.index(label) for label in labels] == sorted(output.index(label) for label in labels)
