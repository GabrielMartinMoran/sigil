"""Shared append only pipeline test support."""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.core.context.context_budget import ContextBudget
from src.core.context.tokens import estimate_messages_tokens

PAYLOAD = 'x' * 4000


def turn(index: int) -> list:
    return [
        ModelResponse(
            parts=[
                ThinkingPart(content=f'thought {index}'),
                ToolCallPart(tool_name='read_file', args={'path': f'f{index}.py'}, tool_call_id=f'c{index}'),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name='read_file',
                    content={'path': f'f{index}.py', 'content': PAYLOAD},
                    tool_call_id=f'c{index}',
                )
            ]
        ),
    ]


def conversation(turns: int) -> list:
    messages: list = [ModelRequest(parts=[UserPromptPart(content='go')])]
    for index in range(turns):
        messages += turn(index)
    return messages


def budget_for(messages: list, *, share: float) -> ContextBudget:
    """A window where the history lands on ``share`` of it."""
    return ContextBudget(window=int(estimate_messages_tokens(messages) / share) + 1, output_reserve=0)


def parts_of(messages) -> list:
    return [part for message in messages for part in message.parts]
