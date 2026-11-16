"""Manual compaction obeys retention without requiring pressure."""

from pydantic_ai.messages import ModelRequest, UserPromptPart

from src.core.context.context_budget import ContextBudget
from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy
from tests.support.context_compaction_records import history


def test_manual_llm_requests_summary_below_pressure() -> None:
    view = AppendOnlyStrategy().view(history(30), ContextBudget(window=1_000_000), manual_compaction='llm')
    assert view.summary_request is not None
    assert view.plan.state.compacted_calls


def test_manual_llm_reports_unavailable_boundary() -> None:
    view = AppendOnlyStrategy().view(
        [ModelRequest(parts=[UserPromptPart('hello')])], ContextBudget(window=1_000_000), manual_compaction='llm'
    )
    assert view.summary_request is None
    assert 'summary_boundary_unavailable' in view.plan.exceptions


def test_manual_summary_is_recorded_and_replayed() -> None:
    import asyncio

    from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from src.core.context.compaction.manual import compact
    from src.core.context.compaction.records import recorded_state

    messages = history(30)
    original_parts = [message.parts for message in messages]
    model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart('A concise checkpoint.')]))
    result = asyncio.run(
        compact(messages, AppendOnlyStrategy(), ContextBudget(window=1_000_000), mode='llm', model=model)
    )
    assert result.status == 'complete'
    assert [message.parts for message in messages] == original_parts
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(messages))
    assert recorded_state(restored)[0].summary == 'A concise checkpoint.'
    assert AppendOnlyStrategy().view(restored, ContextBudget(window=1_000_000)).plan.state.summary
