"""Regression coverage for durable cache epochs and incremental checkpoints."""

from pydantic_ai.messages import BinaryImage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.events import POLICY_VERSION, RECORD_KEY, CompactionReason
from src.engine.context.compaction.summary import Summary, summary_request
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from tests.test_append_only_pipeline import budget_for, conversation


def record_view(messages: list, view) -> list:
    record = {
        'version': POLICY_VERSION,
        'state': view.plan.state.as_row(),
        'events': [event.as_row() for event in view.events],
    }
    return [*messages, ModelResponse(parts=[TextPart('recorded')], metadata={RECORD_KEY: record})]


def test_should_replay_image_removal_without_pressure_after_serialization() -> None:
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    image = BinaryImage(data=b'image', media_type='image/png', identifier='img_old')
    messages = [
        ModelRequest(parts=[UserPromptPart(['look', image])]),
        ModelResponse(parts=[TextPart('seen')]),
        ModelRequest(parts=[UserPromptPart('later')]),
    ]
    strategy = AppendOnlyStrategy(image_turns=1)
    first = strategy.view(messages, ContextBudget(window=10_000, instructions=8_000, output_reserve=0))
    assert first.dropped_images == 1
    restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(record_view(messages, first)))
    replay = strategy.view(restored, ContextBudget(window=100_000, output_reserve=0))
    assert replay.messages[: len(first.messages)] == first.messages
    assert replay.dropped_images == 1
    assert replay.events == first.events


def test_should_record_summary_event_when_checkpoint_is_applied() -> None:
    messages = conversation(30)
    strategy = AppendOnlyStrategy()
    budget = budget_for(messages, share=0.9)
    request = strategy.view(messages, budget).summary_request
    view = strategy.view(messages, budget, summary=Summary('## Goal\nContinue', request.boundary))
    assert view.last_event.reason == CompactionReason.SUMMARY
    assert view.last_event.summary_chars == len('## Goal\nContinue')
    replay = strategy.view(record_view(messages, view), ContextBudget(window=None))
    assert replay.events == view.events


def test_should_only_offer_incremental_span_when_updating_checkpoint() -> None:
    messages = [ModelRequest(parts=[UserPromptPart(f'message {i}')]) for i in range(5)]
    first = summary_request(messages, boundary=None, previous=None, reasoning_blocks=0, tool_calls=0)
    grown = [*messages, ModelRequest(parts=[UserPromptPart('new')])]
    second = summary_request(grown, boundary=first.boundary, previous='checkpoint', reasoning_blocks=0, tool_calls=0)
    assert second.messages == (messages[-1],)
    assert second.previous == 'checkpoint'


def test_should_report_failed_summary_consistently_in_diagnostics() -> None:
    from src.engine.context.capability import _failed_summary

    messages = conversation(30)
    view = AppendOnlyStrategy().view(messages, budget_for(messages, share=0.9))
    failed = _failed_summary(view)
    assert 'summary_failed' in failed.diagnostics['exceptions']
    assert 'summary_pending' not in failed.diagnostics['exceptions']
    assert failed.diagnostics['exceptions'] == failed.plan.exceptions
    assert failed.summary_request is None
