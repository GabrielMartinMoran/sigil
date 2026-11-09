"""Persistence and cancellation boundaries for append-only maintenance."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    BinaryImage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from src.engine.context.budget import ContextBudget
from src.engine.context.capability import ContextViewCapability
from src.engine.context.compaction.events import RECORD_KEY
from src.engine.context.compaction.identity import legacy_part_key, part_key
from src.engine.context.compaction.maintenance import receipts
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.cost import session_cost
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from tests.test_append_only_pipeline import budget_for, conversation


def test_should_distinguish_parts_with_equal_timestamps_after_json_roundtrip() -> None:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    messages = [ModelRequest(parts=[UserPromptPart(text, timestamp=timestamp)]) for text in ('first', 'second')]
    keys = [part_key(message, message.parts[0], 0) for message in messages]
    assert len(set(keys)) == 2
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))
    assert [part_key(message, message.parts[0], 0) for message in restored] == keys


def test_should_migrate_version_three_part_keys_and_summary_boundary() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart('start')]),
        ModelResponse(parts=[ThinkingPart('old'), TextPart('result')]),
        ModelRequest(parts=[UserPromptPart('tail')]),
    ]
    old_reason = legacy_part_key(messages[1], messages[1].parts[0], 0)
    old_boundary = legacy_part_key(messages[2], messages[2].parts[0], 0)
    record = {
        'version': 3,
        'state': {
            'dropped_parts': [old_reason],
            'summary': 'checkpoint',
            'summary_boundary': old_boundary,
            'evaluated_steps': 1,
        },
        'events': [],
        'anchor': old_boundary,
    }
    messages[-1] = replace(messages[-1], metadata={RECORD_KEY: record})
    view = AppendOnlyStrategy().view(messages, ContextBudget(window=100_000))
    assert view.messages[0].parts[0].content == 'checkpoint'
    assert view.messages[1].parts[0].content == 'tail'
    assert view.plan.state.dropped_parts == (part_key(messages[1], messages[1].parts[0], 0),)


def test_should_keep_image_occurrences_distinct_within_merged_request() -> None:
    image_a = BinaryImage(data=b'a', media_type='image/png', identifier='img_a')
    image_b = BinaryImage(data=b'b', media_type='image/png', identifier='img_b')
    messages = [
        ModelRequest(
            parts=[ToolReturnPart('view_image', [image_a], tool_call_id='a'), UserPromptPart(['new', image_b])]
        )
    ]
    from src.engine.context.strategy.rewriting import drop_stale_images

    projected, count = drop_stale_images(messages, turn_window=3, max_tokens=0)
    assert count == 2
    assert 'img_a' in str(projected[0].parts[0].content)
    assert 'img_b' in str(projected[0].parts[1].content)


def test_should_retain_auxiliary_receipt_through_failed_main_request_and_resume() -> None:
    def answer(messages, info):
        if '<conversation>' in str(messages[-1].parts[0].content):
            return ModelResponse(
                parts=[TextPart('checkpoint')],
                usage=RequestUsage(input_tokens=100, output_tokens=10),
                provider_details={'cost': '0.20'},
            )
        raise RuntimeError('main request failed')

    history = conversation(30)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(),
        meter=ContextMeter(window=budget_for(history, share=0.9).window, output_reserve=0),
    )
    agent = Agent(FunctionModel(answer), capabilities=[capability])

    async def interrupted():
        async with agent.iter('continue', message_history=history) as run:
            with pytest.raises(RuntimeError, match='main request failed'):
                async for _ in run:
                    pass
            return run.all_messages()

    failed = asyncio.run(interrupted())
    assert session_cost(failed).total == Decimal('0.20')
    assert len(receipts(failed)) == 1
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(failed))

    def resume(messages, info):
        return ModelResponse(
            parts=[TextPart('done')],
            usage=RequestUsage(input_tokens=100, output_tokens=10),
            provider_details={'cost': '0.03'},
        )

    capability.meter.set_window(1_000_000)
    resumed = asyncio.run(
        Agent(FunctionModel(resume), capabilities=[capability]).run('resume', message_history=restored)
    )
    assert len(receipts(resumed.all_messages())) == 1
    assert session_cost(resumed.all_messages()).total == Decimal('0.23')


def test_should_record_cancelled_summary_as_unmeasured_without_checkpoint() -> None:
    async def cancel(messages, info):
        raise asyncio.CancelledError()

    history = conversation(30)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(),
        meter=ContextMeter(window=budget_for(history, share=0.9).window, output_reserve=0),
    )
    agent = Agent(FunctionModel(cancel), capabilities=[capability])

    async def interrupted():
        async with agent.iter('continue', message_history=history) as run:
            with pytest.raises(asyncio.CancelledError):
                async for _ in run:
                    pass
            return run.all_messages()

    messages = asyncio.run(interrupted())
    assert len(receipts(messages)) == 1
    assert receipts(messages)[0]['status'] == 'cancelled'
    assert session_cost(messages).unmeasured == len(history[1::2]) + 1
    assert 'summary' not in str(receipts(messages)[0]['response']['parts'])


def test_should_keep_ambiguous_reasoning_identities_instead_of_dropping_protected_blocks() -> None:
    from src.engine.context.compaction.coordinator import apply, plan

    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    messages = [ModelResponse(parts=[ThinkingPart('same')], timestamp=timestamp) for _ in range(7)]
    planned = plan(messages, pressure=True)
    projected, _ = apply(messages, planned)
    assert sum(isinstance(part, ThinkingPart) for message in projected for part in message.parts) >= 5


def test_should_refuse_ambiguous_checkpoint_boundary() -> None:
    from src.engine.context.compaction.summary import summary_request

    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    messages = [ModelRequest(parts=[UserPromptPart('same', timestamp=timestamp)]) for _ in range(5)]
    assert summary_request(messages, boundary=None, previous=None, reasoning_blocks=0, tool_calls=0) is None
