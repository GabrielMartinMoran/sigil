"""The checkpoint request is bounded, incremental and separately billable."""

import asyncio
from decimal import Decimal

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    BinaryImage,
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from src.engine.context.budget import ContextBudget
from src.engine.context.capability import ContextViewCapability
from src.engine.context.compaction.summary import summary_boundary
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.cost import session_cost
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from tests.test_append_only_pipeline import budget_for, conversation


def test_should_preserve_native_exchanges_outside_summary() -> None:
    messages = [ModelRequest(parts=[UserPromptPart('start')])]
    messages += [
        ModelResponse(parts=[NativeToolCallPart('web', {}, tool_call_id='native')]),
        ModelResponse(parts=[NativeToolReturnPart('web', 'answer', tool_call_id='native')]),
    ]
    messages += [ModelRequest(parts=[UserPromptPart(str(i))]) for i in range(25)]
    assert summary_boundary(messages, reasoning_blocks=0, tool_calls=0) == 1


def test_should_account_for_checkpoint_usage_after_sqlite_resume(tmp_path) -> None:
    from src.engine.sessions.session_store import SessionStore

    def answer(messages, info):
        summary = '<conversation>' in str(messages[-1].parts[0].content)
        return ModelResponse(
            parts=[TextPart('## Goal\nContinue' if summary else 'done')],
            usage=RequestUsage(input_tokens=100, output_tokens=10),
            provider_details={'cost': '0.20' if summary else '0.03'},
        )

    messages = conversation(30)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(),
        meter=ContextMeter(window=budget_for(messages, share=0.9).window, output_reserve=0),
    )
    run = asyncio.run(Agent(FunctionModel(answer), capabilities=[capability]).run('continue', message_history=messages))
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'home')
    session = store.create_session()
    session.messages = run.all_messages()
    store.save(session)
    assert session_cost(store.load(session.id).messages).total == Decimal('0.23')
    assert run.usage.requests == 2


def test_should_account_for_rejected_summary_response() -> None:
    def answer(messages, info):
        summary = '<conversation>' in str(messages[-1].parts[0].content)
        return ModelResponse(
            parts=[TextPart('partial' if summary else 'done')],
            finish_reason='length' if summary else 'stop',
            usage=RequestUsage(input_tokens=100, output_tokens=10),
            provider_details={'cost': '0.20' if summary else '0.03'},
        )

    messages = conversation(30)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(),
        meter=ContextMeter(window=budget_for(messages, share=0.9).window, output_reserve=0),
    )
    run = asyncio.run(Agent(FunctionModel(answer), capabilities=[capability]).run('continue', message_history=messages))
    assert session_cost(run.all_messages()).total == Decimal('0.23')
    assert capability.last_view.plan.state.summary is None
    assert capability.project(run.all_messages()).summary_error == 'ValueError'


def test_should_not_serialize_binary_bytes_or_history_metadata_into_checkpoint() -> None:
    from src.engine.context.compaction.summary_generator import generate_summary
    from tests.test_compaction_summary_generation import FakeModel

    model = FakeModel(ModelResponse(parts=[TextPart('checkpoint')]))
    image = BinaryImage(data=b'private image bytes', media_type='image/png', identifier='img_safe')
    messages = [ModelRequest(parts=[UserPromptPart(['look', image])], metadata={'private': 'PRIVATE_METADATA'})]
    asyncio.run(generate_summary(model, messages, budget=ContextBudget(window=100_000)))
    sent = str(model.messages)
    assert 'PRIVATE_METADATA' not in sent
    assert 'cHJpdmF0ZSBpbWFnZSBieXRlcw==' not in sent
    assert 'img_safe' in sent


def test_should_reject_overfull_summary_before_network_call() -> None:
    from src.engine.context.compaction.summary_generator import generate_summary
    from tests.test_compaction_summary_generation import FakeModel

    model = FakeModel(ModelResponse(parts=[TextPart('checkpoint')]))
    with pytest.raises(ValueError, match='budget'):
        asyncio.run(
            generate_summary(
                model,
                [ModelRequest(parts=[UserPromptPart('x' * 10_000)])],
                budget=ContextBudget(window=100, output_reserve=10),
            )
        )
    assert model.messages == []
