"""Actual provider adapters serialize reduced parallel exchanges without leaking records."""

from __future__ import annotations

import asyncio
import json

import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from src.engine.context.capability import ContextViewCapability
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.tooling.lod_policies import ALL_POLICIES


def messages() -> list:
    history = [
        ModelRequest(parts=[UserPromptPart(content='read files')]),
        ModelResponse(
            parts=[
                ToolCallPart(tool_name='read_file', args={'path': 'a.py'}, tool_call_id='call_a'),
                ToolCallPart(tool_name='foreign', args={'rows': ['argument' * 1000]}, tool_call_id='call_b'),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name='read_file', content={'path': 'a.py', 'content': 'data ' * 3000}, tool_call_id='call_a'
                ),
                ToolReturnPart(tool_name='foreign', content='external ' * 3000, tool_call_id='call_b'),
            ]
        ),
    ]
    for index in range(30):
        history.extend(
            [
                ModelResponse(parts=[ToolCallPart(tool_name='noop', args={}, tool_call_id=f'noop_{index}')]),
                ModelRequest(parts=[ToolReturnPart(tool_name='noop', content='ok', tool_call_id=f'noop_{index}')]),
            ]
        )
    return history


@pytest.mark.parametrize('adapter', ['chat', 'responses', 'anthropic'])
def test_actual_adapter_preserves_ids_order_and_valid_json_after_lod(adapter):
    captured = []

    def respond(request):
        captured.append(json.loads(request.content))
        if adapter == 'chat':
            payload = {
                'id': 'reply',
                'object': 'chat.completion',
                'created': 1,
                'model': 'test',
                'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}],
                'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
            }
        elif adapter == 'responses':
            payload = {
                'id': 'resp_test',
                'object': 'response',
                'created_at': 1,
                'model': 'test',
                'status': 'completed',
                'output': [
                    {
                        'id': 'msg_test',
                        'type': 'message',
                        'role': 'assistant',
                        'status': 'completed',
                        'content': [{'type': 'output_text', 'text': 'ok', 'annotations': []}],
                    }
                ],
                'usage': {'input_tokens': 1, 'output_tokens': 1, 'total_tokens': 2},
            }
        else:
            payload = {
                'id': 'msg_test',
                'type': 'message',
                'role': 'assistant',
                'model': 'test',
                'content': [{'type': 'text', 'text': 'ok'}],
                'stop_reason': 'end_turn',
                'stop_sequence': None,
                'usage': {'input_tokens': 1, 'output_tokens': 1},
            }
        return httpx2.Response(200, json=payload)

    async def scenario():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            if adapter == 'anthropic':
                model = AnthropicModel('test', provider=AnthropicProvider(api_key='test', http_client=client))
            else:
                provider = OpenAIProvider(api_key='test', http_client=client)
                model = (
                    OpenAIChatModel('test', provider=provider)
                    if adapter == 'chat'
                    else OpenAIResponsesModel('test', provider=provider)
                )
            capability = ContextViewCapability(
                strategy=WindowedStrategy(lookup=ALL_POLICIES.get), meter=ContextMeter(window=100000)
            )
            agent = Agent(model, capabilities=[capability])
            result = await agent.run('continue', message_history=messages())
            assert result.output == 'ok'
            assert capability.last_view.truncated_results >= 3

    asyncio.run(scenario())
    assert len(captured) == 1
    serialized = json.dumps(captured[0])
    assert 'sigil_context' not in serialized
    assert 'sigil_view_position' not in serialized
    assert 'call_a' in serialized and 'call_b' in serialized
    assert serialized.index('call_a') < serialized.index('call_b')
    assert 'truncated' in serialized or 'omitted_fields' in serialized
    assert 'external ' * 3000 not in serialized
    assert 'data ' * 3000 not in serialized
