"""Human tool presentation must survive model serialization and session reloads."""

from __future__ import annotations

from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ToolReturnPart

from src.server.api.views import transcript
from src.ui.widgets.chat.tool_call_block import ToolCallBlock


def test_should_render_persisted_tool_results_identically_in_both_clients() -> None:
    messages = [
        ModelRequest(parts=[ToolReturnPart('run', [{'returncode': 0, 'stdout': '9 passed\n'}], tool_call_id='a')])
    ]
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))
    web = transcript(restored)[0]
    assert web['text'] == '9 passed'
    assert web['summary']['text'] == '9 passed'
    tui = ToolCallBlock('run', {})
    tui.set_result(restored[0].parts[0].content)
    assert tui._summary.text == web['summary']['text']
    assert 'stdout' not in '\n'.join(line.plain for line in tui._body())
