"""An emergency with no scheduled boundary still points at the real step."""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.events import CompactionReason
from src.engine.context.strategy.windowed import WindowedStrategy


def _heavy_turn(index: int) -> list:
    return [
        ModelRequest(parts=[UserPromptPart(content=f'request {index}')]),
        ModelResponse(
            parts=[ToolCallPart(tool_name='read_file', args={'path': f'f{index}'}, tool_call_id=f'c{index}')]
        ),
        ModelRequest(parts=[ToolReturnPart(tool_name='read_file', content='x' * 8_000, tool_call_id=f'c{index}')]),
    ]


def test_the_emergency_event_names_the_step_the_execution_reached():
    messages = [*_heavy_turn(1), *_heavy_turn(2), *_heavy_turn(3)]
    # A window too small even for the reduced history: turns have to go.
    result = WindowedStrategy().view(messages, ContextBudget(window=250, output_reserve=0))
    assert result.dropped_messages > 0
    emergency = [event for event in result.events if event.reason is CompactionReason.EMERGENCY]
    assert len(emergency) == 1
    # Three model responses, so the emergency sits at step 3 and not at 0.
    assert emergency[0].step == 3
    assert 'window' in emergency[0].unmet


def test_a_request_that_fits_has_no_emergency_event():
    messages = [*_heavy_turn(1), *_heavy_turn(2)]
    result = WindowedStrategy().view(messages, ContextBudget(window=1_000_000, output_reserve=0))
    assert result.dropped_messages == 0
    assert all(event.reason is not CompactionReason.EMERGENCY for event in result.events)
