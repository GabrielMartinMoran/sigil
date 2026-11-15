"""Shared context compaction records test support."""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from src.engine.context.capability import ContextViewCapability
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from src.engine.context.tokens import estimate_messages_tokens


def history(count: int) -> list:
    messages = [ModelRequest(parts=[UserPromptPart(content='go')], run_id='past')]
    for i in range(count):
        messages.extend(
            [
                ModelResponse(
                    parts=[
                        ThinkingPart(content=f'thought {i}'),
                        ToolCallPart(tool_name='noop', args={}, tool_call_id=f'c{i}'),
                    ],
                    run_id='past',
                ),
                ModelRequest(
                    parts=[ToolReturnPart(tool_name='noop', content='ok', tool_call_id=f'c{i}')], run_id='past'
                ),
            ]
        )
    return messages


def pressured_window(messages: list) -> int:
    """A window the history crosses at the trigger while still fitting the hard budget."""
    return int(estimate_messages_tokens(messages) * 1.1) + 1


def run_recorded(messages: list, *, window: int | None = None) -> list:
    window = pressured_window(messages) if window is None else window
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(), meter=ContextMeter(window=window, output_reserve=0)
    )
    agent = Agent(TestModel(), capabilities=[capability])
    return asyncio.run(agent.run('continue', message_history=messages)).all_messages()
