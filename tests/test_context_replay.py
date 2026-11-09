"""A local replay reports limitations instead of inventing provider cache hits."""

from decimal import Decimal

from pydantic_ai.messages import ModelMessagesTypeAdapter

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.policy import CompactionPolicy
from src.engine.context.replay import replay
from tests.test_append_only_pipeline import conversation


def test_should_replay_without_mutating_source_transcript() -> None:
    messages = conversation(30)
    original = ModelMessagesTypeAdapter.dump_json(messages)
    report = replay(messages, ContextBudget(window=100_000, output_reserve=0), CompactionPolicy())
    assert report.requests == 30
    assert report.compactions == 0
    assert 0 < report.common_prefix_tokens < report.input_tokens
    assert ModelMessagesTypeAdapter.dump_json(messages) == original
    assert report.input_cost(uncached=Decimal(1), cached=Decimal('0.1')) > 0


def test_should_report_pending_summaries_without_calling_a_model() -> None:
    report = replay(conversation(30), ContextBudget(window=20_000, output_reserve=0), CompactionPolicy())
    assert report.summary_requests > 0
    assert report.blocked
    assert report.requests < 30


def test_should_not_price_a_request_rejected_by_safety_budget() -> None:
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

    messages = [ModelRequest(parts=[UserPromptPart('x' * 10_000)]), ModelResponse(parts=[TextPart('done')])]
    report = replay(messages, ContextBudget(window=100, output_reserve=0), CompactionPolicy())
    assert report.blocked
    assert report.requests == 1
    assert report.input_tokens == 0
    assert report.input_cost(uncached=Decimal(1), cached=Decimal('0.1')) == 0
