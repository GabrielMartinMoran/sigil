"""Tests for the hub and the question tool."""

import asyncio

import pytest

from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.interaction.models.question_option import QuestionOption
from src.engine.interaction.tools import register_interaction_tools


class DummyAgent:
    def __init__(self) -> None:
        self.tools = {}

    def _register(self, func, kwargs):
        name = kwargs.get('name') or func.__name__
        self.tools[name] = func
        return func

    def tool_plain(self, func, **kwargs):
        return self._register(func, kwargs)

    def tool(self, func, **kwargs):
        return self._register(func, kwargs)


def _question():
    return QuestionItem(
        question='What do we do?',
        options=[QuestionOption(label='A', recommended=True), QuestionOption(label='B')],
    )


def _tool(hub):
    agent = DummyAgent()
    register_interaction_tools(agent, hub)
    return agent.tools['ask_user']


def test_ask_user_returns_answers():
    async def asker(questions):
        return [QuestionAnswer(question=questions[0].question, answer='A')]

    hub = QuestionHub()
    hub.set_asker(asker)
    tool = _tool(hub)
    result = asyncio.run(tool(questions=[_question()]))
    assert result.cancelled is False
    assert result.answers[0].answer == 'A'


def test_ask_user_cancelled():
    async def asker(questions):
        return None

    hub = QuestionHub()
    hub.set_asker(asker)
    tool = _tool(hub)
    result = asyncio.run(tool(questions=[_question()]))
    assert result.cancelled is True
    assert result.answers == []


class _RecordingContext:
    """Just enough of a `RunContext` to know whether cancellation was asked for."""

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


def test_cancelling_the_panel_cancels_the_run():
    async def asker(questions):
        return None

    hub = QuestionHub()
    hub.set_asker(asker)
    tool = _tool(hub)
    ctx = _RecordingContext()
    asyncio.run(tool(ctx, questions=[_question()]))
    assert ctx.cancelled is True


def test_answering_does_not_cancel_the_run():
    async def asker(questions):
        return [QuestionAnswer(question=questions[0].question, answer='A')]

    hub = QuestionHub()
    hub.set_asker(asker)
    tool = _tool(hub)
    ctx = _RecordingContext()
    result = asyncio.run(tool(ctx, questions=[_question()]))
    assert ctx.cancelled is False
    assert result.cancelled is False


def test_ask_user_without_ui_raises():
    hub = QuestionHub()
    tool = _tool(hub)
    with pytest.raises(RuntimeError, match='not available'):
        asyncio.run(tool(questions=[_question()]))


def test_empty_questions_raises():
    async def asker(questions):
        return []

    hub = QuestionHub()
    hub.set_asker(asker)
    tool = _tool(hub)
    with pytest.raises(ValueError, match='cannot be empty'):
        asyncio.run(tool(questions=[]))
