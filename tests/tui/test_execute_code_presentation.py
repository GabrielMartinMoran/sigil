"""Code executions show their complete source in the expanded tool block."""

import json

from rich.console import Console

from src.ui.widgets.chat.tool_call_block import ToolCallBlock


def rendered(block: ToolCallBlock) -> str:
    console = Console(width=100, force_terminal=True, color_system='truecolor', record=True)
    console.print(block._build())
    return console.export_text(styles=True)


def test_expanded_code_is_visible_while_pending_and_after_error() -> None:
    source = 'async def main():\n    return "hello"\n'
    block = ToolCallBlock('execute_code', {'code': source}, expanded=True)
    pending = rendered(block)
    assert 'async' in pending and 'return' in pending and 'hello' in pending
    assert '\x1b[' in pending
    block.set_error('failed')
    after = rendered(block)
    assert 'hello' in after and 'failed' in after


def test_serialized_arguments_preserve_source_in_expanded_history() -> None:
    source = 'print("first")\n' + '\n'.join(f'print({i})' for i in range(300))
    block = ToolCallBlock('execute_code', json.dumps({'code': source}), expanded=True)
    block.set_result('done')
    result = rendered(block)
    assert 'first' in result and '299' in result and 'done' in result
