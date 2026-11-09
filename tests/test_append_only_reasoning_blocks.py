"""Pressure retention counts reasoning parts, not response envelopes."""

from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart

from src.engine.context.compaction.coordinator import apply, plan
from src.engine.context.compaction.events import POLICY_VERSION, RECORD_KEY, CompactionState


def test_pressure_retains_the_five_newest_reasoning_parts_in_one_response() -> None:
    messages = [ModelResponse(parts=[*[ThinkingPart(content=f'reason {i}') for i in range(7)], TextPart('answer')])]

    decision = plan(messages, pressure=True)
    projected, _ = apply(messages, decision)
    retained = [part.content for part in projected[0].parts if isinstance(part, ThinkingPart)]

    assert retained == [f'reason {i}' for i in range(2, 7)]
    assert CompactionState.from_row(decision.state.as_row()) == decision.state


def test_a_recorded_decision_replays_without_dropping_the_retained_parts() -> None:
    messages = [ModelResponse(parts=[*[ThinkingPart(content=f'reason {i}') for i in range(7)], TextPart('answer')])]
    decision = plan(messages, pressure=True)
    record = {
        'version': POLICY_VERSION,
        'state': decision.state.as_row(),
        'events': [event.as_row() for event in decision.events],
    }
    messages.append(ModelResponse(parts=[TextPart('continued')], metadata={RECORD_KEY: record}))

    replay = plan(messages)
    projected, _ = apply(messages, replay)
    retained = [part.content for message in projected for part in message.parts if isinstance(part, ThinkingPart)]

    assert retained == [f'reason {i}' for i in range(2, 7)]
    assert replay.state == decision.state
