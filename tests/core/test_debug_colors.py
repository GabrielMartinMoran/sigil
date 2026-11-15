"""Debug visuals retain numeric meaning, request identity, and privacy."""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.usage import RequestUsage

from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.debug import build_debug_report
from src.engine.context.meter.request import snapshot_request


def report():
    messages = [
        ModelRequest(parts=[UserPromptPart('PRIVATE')]),
        ModelResponse(parts=[TextPart('PRIVATE')], usage=RequestUsage(input_tokens=100, cache_read_tokens=90)),
        ModelResponse(parts=[TextPart('PRIVATE')], usage=RequestUsage(input_tokens=200, cache_read_tokens=30)),
    ]
    meter = ContextMeter(window=1000)
    return build_debug_report(messages, snapshot=meter.snapshot(messages), request=snapshot_request(None))


def test_unknown_and_native_parts_have_a_row_instead_of_silently_disappearing():
    from pydantic_ai.messages import NativeToolCallPart

    snapshot = snapshot_request(
        ModelRequestParameters(),
        messages=[
            ModelResponse(parts=[NativeToolCallPart('web_search', args={'PRIVATE': 'PRIVATE'}, tool_call_id='c')])
        ],
    )
    assert snapshot.sections[0].kind == 'tool_call'
    assert snapshot.sections[0].tokens is None
    assert 'PRIVATE' not in repr(snapshot)


def test_real_agent_snapshot_excludes_the_response_it_has_not_received_yet():
    import asyncio

    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from src.engine.context.capability import ContextViewCapability
    from src.engine.context.strategy.append_only import AppendOnlyStrategy

    async def run():
        cap = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter(window=100000))
        agent = Agent(TestModel(custom_output_text='PRIVATE'), capabilities=[cap])
        first = await agent.run('PRIVATE')
        first_snapshot = cap.last_request
        assert [s.kind for s in first_snapshot.sections] == ['user']
        await agent.run('PRIVATE', message_history=first.all_messages())
        assert [s.kind for s in cap.last_request.sections] == ['user', 'text', 'user']
        assert first_snapshot.sections == first_snapshot.sections[:1]

    asyncio.run(run())


def test_plain_text_cache_uses_the_same_descending_order():
    from src.engine.context.meter.debug_render import render_debug_report

    text = render_debug_report(report())
    assert text.index(' | 2 |') < text.index(' | 1 |')
