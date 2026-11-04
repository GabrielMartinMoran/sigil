"""Tests for the concurrency guards between sibling tools."""

import asyncio

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.engine.agents.tool_errors import ToolErrorRecovery
from src.engine.tooling.concurrency import (
    ConcurrencyContext,
    SiblingCall,
    ToolConflict,
    earlier_siblings,
)
from src.engine.tooling.registrar import ToolRegistrar


def _response(*calls: tuple[str, dict, str | None]) -> ModelResponse:
    return ModelResponse(
        parts=[
            ToolCallPart(name, args, tool_call_id=call_id) if call_id else ToolCallPart(name, args)
            for name, args, call_id in calls
        ]
    )


# --- earlier_siblings ---------------------------------------------------------


def test_earlier_siblings_returns_only_the_previous_calls_in_order():
    messages: list[ModelMessage] = [
        ModelRequest(parts=[UserPromptPart('hello')]),
        _response(('a', {'x': 1}, 'c1'), ('b', {'x': 2}, 'c2'), ('c', {'x': 3}, 'c3')),
    ]

    siblings = earlier_siblings(messages, tool_call_id='c3', tool_name='c', args={'x': 3})

    assert [sibling.tool_name for sibling in siblings] == ['a', 'b']
    assert [sibling.tool_call_id for sibling in siblings] == ['c1', 'c2']


def test_first_call_has_no_siblings():
    messages: list[ModelMessage] = [_response(('a', {}, 'c1'), ('b', {}, 'c2'))]

    assert earlier_siblings(messages, tool_call_id='c1', tool_name='a', args={}) == ()


def test_no_siblings_without_messages():
    assert earlier_siblings([], tool_call_id='c1', tool_name='a', args={}) == ()


def test_no_siblings_when_the_last_message_is_not_a_response():
    messages: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart('hello')])]

    assert earlier_siblings(messages, tool_call_id='c1', tool_name='a', args={}) == ()


def test_falls_back_to_name_and_args_when_the_id_does_not_match():
    messages: list[ModelMessage] = [_response(('a', {'x': 1}, 'c1'), ('a', {'x': 2}, 'c2'))]

    siblings = earlier_siblings(messages, tool_call_id='stale-id', tool_name='a', args={'x': 2})

    assert [sibling.args for sibling in siblings] == [{'x': 1}]


def test_unknown_call_yields_no_siblings():
    messages: list[ModelMessage] = [_response(('a', {}, 'c1'))]

    assert earlier_siblings(messages, tool_call_id='nope', tool_name='zzz', args={}) == ()


def test_non_dict_args_are_ignored():
    response = ModelResponse(
        parts=[
            ToolCallPart('a', 'raw-json-string', tool_call_id='c1'),
            ToolCallPart('b', {}, tool_call_id='c2'),
        ]
    )

    siblings = earlier_siblings([response], tool_call_id='c2', tool_name='b', args={})

    assert siblings == (SiblingCall(tool_name='a', args={}, tool_call_id='c1'),)


# --- Wrapper ------------------------------------------------------------------


def _same_key_conflict(ctx: ConcurrencyContext) -> str | None:
    for sibling in ctx.siblings:
        if sibling.tool_name == ctx.tool_name and sibling.args.get('key') == ctx.args.get('key'):
            return f'{ctx.tool_name} already ran for key {ctx.args.get("key")!r}'
    return None


def _agent_with_guarded_tool(calls: list[tuple[str, dict]]) -> Agent:
    def model_function(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart(name, args) for name, args in calls])
        return ModelResponse(parts=[TextPart('ok')])

    agent = Agent(FunctionModel(model_function), instructions='x', capabilities=[ToolErrorRecovery()])
    registrar = ToolRegistrar(agent)

    @registrar.tool(concurrency=_same_key_conflict)
    def touch(key: str, mode: str = 'default') -> str:
        return f'{key}:{mode}'

    return agent


def _returns(result):
    from pydantic_ai.messages import ToolReturnPart

    return [
        part
        for message in result.all_messages()
        for part in getattr(message, 'parts', [])
        if isinstance(part, ToolReturnPart)
    ]


def test_guard_lets_the_first_call_through_and_fails_the_second():
    agent = _agent_with_guarded_tool([('touch', {'key': 'a'}), ('touch', {'key': 'a'})])

    result = asyncio.run(agent.run('go'))

    returns = _returns(result)
    assert [part.outcome for part in returns] == ['success', 'failed']
    assert 'already ran for key' in str(returns[1].content)


def test_guard_does_not_block_calls_with_a_different_key():
    agent = _agent_with_guarded_tool([('touch', {'key': 'a'}), ('touch', {'key': 'b'})])

    result = asyncio.run(agent.run('go'))

    assert [part.outcome for part in _returns(result)] == ['success', 'success']


def test_guard_sees_defaults_applied():
    seen: list[dict] = []

    def guard(ctx: ConcurrencyContext) -> str | None:
        seen.append(dict(ctx.args))
        return None

    def model_function(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart('touch', {'key': 'a'})])
        return ModelResponse(parts=[TextPart('ok')])

    agent = Agent(FunctionModel(model_function), instructions='x')
    registrar = ToolRegistrar(agent)

    @registrar.tool(concurrency=guard)
    def touch(key: str, mode: str = 'default') -> str:
        return key

    asyncio.run(agent.run('go'))

    assert seen == [{'key': 'a', 'mode': 'default'}]


def test_guarded_tool_hides_the_context_from_the_schema():
    agent = _agent_with_guarded_tool([])
    tool = agent._function_toolset.tools['touch']

    assert sorted(tool.function_schema.json_schema['properties']) == ['key', 'mode']
    assert tool.sequential is False


def test_direct_call_without_context_skips_the_guard():
    agent = _agent_with_guarded_tool([])
    tool = agent._function_toolset.tools['touch']

    # Without a RunContext there are no siblings: the direct call runs anyway.
    assert tool.function('a') == 'a:default'


def test_tool_conflict_is_an_exception():
    assert issubclass(ToolConflict, Exception)
    with pytest.raises(ToolConflict, match='boom'):
        raise ToolConflict('boom')


def test_conflict_is_logged_as_an_error():
    events = []

    def model_function(messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart('touch', {'key': 'a'}), ToolCallPart('touch', {'key': 'a'})])
        return ModelResponse(parts=[TextPart('ok')])

    agent = Agent(FunctionModel(model_function), instructions='x', capabilities=[ToolErrorRecovery()])
    registrar = ToolRegistrar(agent, events.append)

    @registrar.tool(concurrency=_same_key_conflict)
    def touch(key: str) -> str:
        return key

    asyncio.run(agent.run('go'))

    # The two calls are concurrent: the order between them is not guaranteed.
    phases = sorted(event.phase for event in events)
    assert phases == ['error', 'start', 'start', 'success']
    errors = [event.error or '' for event in events if event.phase == 'error']
    assert len(errors) == 1
    assert 'already ran for key' in errors[0]
