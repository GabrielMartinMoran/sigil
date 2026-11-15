"""The step budget: a run that uses up its calls still gets to explain itself.

The behaviour is the point of the feature, so it is tested against a real
``Agent.iter`` and not against the capability in isolation: the ordering between
``prepare_tools`` and ``before_model_request`` is what makes the last call the one
that has no tools, and that ordering belongs to the library.
"""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits

from src.engine.agents.step_budget import PREFIX, StepBudget, notice


def _agent(seen: list[list[str]]) -> Agent:
    """An agent whose model always calls a tool while it has one."""

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        names = [tool.name for tool in info.function_tools]
        seen.append(names)
        if names:
            return ModelResponse(parts=[ToolCallPart('ping', {})])
        return ModelResponse(parts=[TextPart('what I found, and what is left')])

    agent = Agent(FunctionModel(model))

    @agent.tool_plain
    def ping() -> str:
        return 'pong'

    return agent


def _run(agent: Agent, budget: StepBudget):
    async def inner():
        async with agent.iter('go', capabilities=[budget], usage_limits=UsageLimits(request_limit=None)) as run:
            async for _node in run:
                pass
        return run

    return asyncio.run(inner())


def test_the_run_gets_one_extra_call_without_tools():
    seen: list[list[str]] = []
    budget = StepBudget(max_steps=2)
    run = _run(_agent(seen), budget)

    # Two calls with the tool, then the extra one with nothing to call.
    assert seen == [['ping'], ['ping'], []]
    assert budget.exhausted
    assert run.result is not None and run.result.output == 'what I found, and what is left'


def test_the_notice_travels_with_the_last_request_and_stays_in_the_history():
    seen: list[list[str]] = []
    budget = StepBudget(max_steps=1)
    run = _run(_agent(seen), budget)

    history = run.all_messages()
    texts = [
        str(part.content)
        for message in history
        for part in getattr(message, 'parts', ())
        if getattr(part, 'part_kind', '') == 'user-prompt'
    ]
    assert any(PREFIX in text for text in texts)
    assert len(seen) == 2 and seen[-1] == []


def test_the_notice_says_what_to_answer_with():
    text = notice(7)
    assert PREFIX in text
    assert '7 steps' in text
    assert 'no more tools will run' in text
    # The shape of the answer has to be asked for, or the model apologizes instead.
    assert 'what you established' in text
    assert 'what you did not get to' in text


def test_the_notice_names_the_role_it_is_talking_to():
    assert 'this subagent' in notice(3, role='subagent')
    assert 'this turn' in notice(3, role='turn')


def test_a_run_that_fits_its_budget_is_untouched():
    seen: list[list[str]] = []
    calls = {'n': 0}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls['n'] += 1
        seen.append([tool.name for tool in info.function_tools])
        if calls['n'] == 1:
            return ModelResponse(parts=[ToolCallPart('ping', {})])
        return ModelResponse(parts=[TextPart('done')])

    agent = Agent(FunctionModel(model))

    @agent.tool_plain
    def ping() -> str:
        return 'pong'

    budget = StepBudget(max_steps=10)
    run = _run(agent, budget)

    assert not budget.exhausted
    assert all(names == ['ping'] for names in seen)
    assert run.result is not None and run.result.output == 'done'


def test_a_budget_of_zero_is_refused_at_construction():
    import pytest

    with pytest.raises(ValueError, match='at least 1'):
        StepBudget(max_steps=0)
