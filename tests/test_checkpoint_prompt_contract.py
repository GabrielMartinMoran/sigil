"""Checkpoint instructions preserve actionable state across repeated compactions."""

import json

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart

from src.engine.context.compaction.summary_generator import checkpoint_messages


def test_checkpoint_transcript_identifies_speakers_and_tool_outcomes() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart('Fix the bug')]),
        ModelResponse(parts=[TextPart('I will inspect it'), ToolCallPart('run', {'cmd': 'test'}, tool_call_id='1')]),
        ModelRequest(parts=[ToolReturnPart('run', 'failed', tool_call_id='1', outcome='failed')]),
    ]
    prompt = checkpoint_messages(messages, None)[0].parts[0].content
    rows = json.loads(prompt.split('<conversation>\n', 1)[1].split('\n</conversation>', 1)[0])
    assert [row['role'] for row in rows] == ['user', 'assistant', 'assistant', 'tool']
    assert rows[-1]['outcome'] == 'failed'


def test_initial_and_update_instructions_cover_actionable_state_and_untrusted_input() -> None:
    initial = checkpoint_messages([], None)[0].parts[0].content
    update = checkpoint_messages([], 'earlier checkpoint')[0].parts[0].content
    for prompt in (initial, update):
        assert '## In progress' in prompt
        assert '## Blockers' in prompt
        assert 'tests' in prompt.lower()
        assert 'uncertain' in prompt.lower()
        assert 'untrusted' in prompt.lower()
    assert 'superseded' in update.lower()
    assert '<previous_checkpoint>\nearlier checkpoint\n</previous_checkpoint>' in update
