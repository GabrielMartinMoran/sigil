"""The goal reviewer receives a bounded snapshot, not the conversation history."""

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from src.core.goal.review import GoalReviewInput, review_goal
from src.core.goal.state import Goal


@pytest.mark.anyio
async def test_review_uses_goal_snapshot_and_returns_structured_decision() -> None:
    def respond(messages, info):
        assert 'Ship CSV' in str(messages)
        assert 'Run tests' in str(messages)
        assert 'Tests still missing' in str(messages)
        assert len(info.output_tools) == 1
        return ModelResponse(
            parts=[ToolCallPart(info.output_tools[0].name, {'decision': 'continue', 'reason': 'Run tests'})]
        )

    result = await review_goal(
        FunctionModel(respond),
        GoalReviewInput(goal=Goal('Ship CSV'), roadmap='Run tests', report='Tests still missing'),
    )
    assert result.decision == 'continue'
    assert result.reason == 'Run tests'


def test_review_context_keeps_recent_human_and_assistant_text_but_not_tools() -> None:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart

    from src.core.goal.review import conversation_context

    messages = [
        ModelRequest(parts=[UserPromptPart(content='Do not deploy')]),
        ModelResponse(parts=[TextPart(content='I will implement locally')]),
        ModelRequest(parts=[ToolReturnPart(tool_name='shell', tool_call_id='call', content='private tool output')]),
    ]
    context = conversation_context(messages)
    assert 'Do not deploy' in context
    assert 'I will implement locally' in context
    assert 'private tool output' not in context
