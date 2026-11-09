"""A changed instruction prefix can compact on adjacent steps of one turn."""

import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from src.engine.context.capability import ContextViewCapability
from src.engine.context.compaction.records import recorded_state
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.debug import debug_turns
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from tests.test_context_compaction_records import history


def test_changed_instructions_at_adjacent_steps_record_distinct_events() -> None:
    instructions = ['initial']
    calls = 0

    def respond(messages, info):
        nonlocal calls
        calls += 1
        if calls <= 2:
            return ModelResponse(parts=[ToolCallPart('change', {}, tool_call_id=f'new-{calls}')])
        return ModelResponse(parts=[TextPart('done')])

    capability = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter(window=1_000_000))
    agent = Agent(FunctionModel(respond), instructions=lambda: instructions[0], capabilities=[capability])

    @agent.tool_plain
    def change() -> str:
        instructions[0] += ' changed'
        return 'ok'

    messages = asyncio.run(agent.run('continue', message_history=history(30))).all_messages()
    state, events = recorded_state(messages)
    turn = debug_turns(messages, events)[-1]
    assert len(events) == 2
    assert len(state.compacted_calls) == 12
    assert [len(step_events) for step_events in turn.step_events] == [0, 1, 1]
    assert events[0].request_id != events[1].request_id
