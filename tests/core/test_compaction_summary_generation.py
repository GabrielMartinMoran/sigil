"""Summary generation must not accept incomplete or tool-calling responses."""

import asyncio

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart

from src.core.context.compaction.summary_generator import generate_summary
from src.core.context.context_budget import ContextBudget
from tests.support.compaction_summary_generation import (
    FakeModel,
)


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
