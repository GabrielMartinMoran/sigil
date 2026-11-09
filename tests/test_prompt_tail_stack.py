"""Volatile agent context travels only at the end of the model request."""

from __future__ import annotations

import asyncio

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from src.engine.agents.agent import create_agent
from src.engine.agents.tree.summoner import SigilDeps
from src.engine.context.capability import ContextViewCapability
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from tests.test_context_compaction_records import history


def test_tail_is_transient_after_history_and_refreshed_after_tool() -> None:
    state = ['pending notice']
    requests = []
    instructions = []

    def respond(messages, info):
        requests.append(messages)
        instructions.append(info.instructions or '')
        if len(requests) == 1:
            return ModelResponse(parts=[ToolCallPart('ack', {}, tool_call_id='ack')])
        return ModelResponse(parts=[TextPart('done')])

    capability = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter(window=100_000))
    agent = create_agent(model=FunctionModel(respond), node_blocks=lambda _: state[0], extra_capabilities=[capability])

    @agent.tool_plain
    def ack() -> str:
        state[0] = 'acknowledged notice'
        return 'ok'

    result = asyncio.run(agent.run('hello', deps=SigilDeps(), message_history=history(30)))
    assert all('pending notice' not in item and 'acknowledged notice' not in item for item in instructions)
    assert 'pending notice' in str(requests[0][-1].parts[-1].content)
    assert 'acknowledged notice' in str(requests[1][-1].parts[-1].content)
    assert 'pending notice' not in str(requests[1])
    assert 'acknowledged notice' not in str(result.all_messages())
    assert capability.last_request.sections[-1].kind == 'dynamic'
    assert capability.last_request.sections[-1].label == 'Agent context'
    assert not capability.last_view.events


def test_empty_tail_is_omitted() -> None:
    captured = []

    def respond(messages, info):
        captured.extend(messages)
        return ModelResponse(parts=[TextPart('done')])

    agent = create_agent(model=FunctionModel(respond), node_blocks=lambda _: '  ')
    asyncio.run(agent.run('hello', deps=SigilDeps()))
    assert len(captured[-1].parts) == 1
    assert captured[-1].parts[0].content == 'hello'


def test_openai_mapping_keeps_tool_response_before_agent_context() -> None:
    from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    model = OpenAIChatModel('test-model', provider=OpenAIProvider(api_key='test-key'))
    messages = [
        ModelRequest(parts=[ToolReturnPart('ack', 'ok', tool_call_id='call')]),
        ModelRequest(parts=[UserPromptPart('<agent_context>current state</agent_context>')]),
    ]

    async def mapped():
        return [item for message in messages async for item in model._map_user_message(message)]

    result = asyncio.run(mapped())
    assert [message['role'] for message in result] == ['tool', 'user']
    assert result[-1]['content'] == '<agent_context>current state</agent_context>'
