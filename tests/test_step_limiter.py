"""Tests for the hard step limit per turn."""

import pytest
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
)

from src.engine.agents.step_limiter import StepLimiter, StepLimitExceeded


def _message_start():
    return PartStartEvent(index=0, part=TextPart(content=''))


def _tool_call():
    return FunctionToolCallEvent(part=ToolCallPart(tool_name='run', args={}, tool_call_id='c1'))


def test_counts_model_messages_and_tool_calls():
    limiter = StepLimiter(max_steps=10)
    limiter.count(_message_start())  # 1
    limiter.count(_tool_call())  # 2
    limiter.count(_message_start())  # 3
    assert limiter.steps == 3


def test_ignores_non_step_events():
    limiter = StepLimiter(max_steps=10)
    limiter.count(PartStartEvent(index=1, part=TextPart(content='x')))  # not a message start
    limiter.count(PartDeltaEvent(index=0, delta=TextPartDelta(content_delta='x')))
    assert limiter.steps == 0


def test_allows_exactly_max_steps():
    limiter = StepLimiter(max_steps=2)
    limiter.count(_message_start())
    limiter.count(_tool_call())
    assert limiter.steps == 2


def test_raises_when_exceeding_limit():
    limiter = StepLimiter(max_steps=2)
    limiter.count(_message_start())
    limiter.count(_tool_call())
    with pytest.raises(StepLimitExceeded) as excinfo:
        limiter.count(_message_start())
    assert excinfo.value.limit == 2


def test_rejects_invalid_limit():
    with pytest.raises(ValueError):
        StepLimiter(max_steps=0)
