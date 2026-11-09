"""Summary generation must not accept incomplete or tool-calling responses."""

import asyncio

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.summary_generator import generate_summary


class FakeModel:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.messages = []

    async def request(self, messages, model_settings, model_request_parameters):
        self.messages = messages
        assert not model_request_parameters.function_tools
        return self.response


def test_summary_request_is_standalone_and_returns_text() -> None:
    model = FakeModel(ModelResponse(parts=[TextPart('## Goal\nContinue work')]))
    result = asyncio.run(
        generate_summary(
            model, [ModelRequest(parts=[UserPromptPart('work on module')])], budget=ContextBudget(window=100_000)
        )
    )
    assert result == '## Goal\nContinue work'
    assert 'work on module' in str(model.messages)


@pytest.mark.parametrize(
    'response',
    [
        ModelResponse(parts=[TextPart('partial')], finish_reason='length'),
        ModelResponse(parts=[ToolCallPart('run', {}, tool_call_id='1')]),
        ModelResponse(parts=[TextPart('  ')]),
    ],
)
def test_invalid_summary_is_rejected(response: ModelResponse) -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            generate_summary(
                FakeModel(response),
                [ModelRequest(parts=[UserPromptPart('work')])],
                budget=ContextBudget(window=100_000),
            )
        )
