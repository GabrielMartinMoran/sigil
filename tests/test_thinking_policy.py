"""Signed reasoning is replayed only to the provider that produced it."""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, ThinkingPart, UserPromptPart

from src.engine.context.capability import compatible_model_history
from src.engine.context.compaction.coordinator import apply, plan
from src.engine.context.strategy.rewriting import accepts_replayed_reasoning


def _thinking(content: str, *, signature: str | None = None, provider: str | None = None) -> ThinkingPart:
    return ThinkingPart(content=content, signature=signature, provider_name=provider)


def _response(part: ThinkingPart) -> ModelResponse:
    return ModelResponse(parts=[part])


def test_signed_reasoning_from_another_provider_is_not_accepted() -> None:
    part = _thinking('signed', signature='sig', provider='anthropic')
    assert accepts_replayed_reasoning(part, 'openai') is False
    assert accepts_replayed_reasoning(part, 'anthropic') is True


def test_unsigned_reasoning_travels_to_any_provider() -> None:
    assert accepts_replayed_reasoning(_thinking('plain'), 'openai') is True


def test_an_unknown_receiver_trusts_the_part() -> None:
    assert accepts_replayed_reasoning(_thinking('signed', signature='sig', provider='anthropic'), None) is True


def test_signed_reasoning_of_another_model_is_removed_without_touching_the_log() -> None:
    messages = [
        ModelRequest(parts=[UserPromptPart('go')]),
        ModelResponse(parts=[_thinking('foreign', signature='sig', provider='anthropic')], model_name='other'),
        ModelResponse(parts=[_thinking('mine')], model_name='mine'),
    ]
    projected = compatible_model_history(messages, 'mine')
    assert len(projected) == 2
    assert len(messages) == 3


def test_foreign_reasoning_is_not_replayed_by_the_view() -> None:
    messages = [
        ModelResponse(parts=[_thinking('foreign', signature='sig', provider='anthropic')]),
        ModelResponse(parts=[_thinking('mine')]),
    ]
    projected, _ = apply(messages, plan(messages, provider='openai'), provider='openai')
    assert [part.content for message in projected for part in message.parts] == ['mine']
