"""Dynamic instruction changes offer a safe deterministic compaction boundary."""

from __future__ import annotations

import asyncio

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from src.engine.context.capability import ContextViewCapability
from src.engine.context.compaction.records import recorded_state
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from tests.test_context_compaction_records import history


def test_changed_instructions_compact_once_without_a_summary_after_resume() -> None:
    instructions = ['initial']
    capability = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter(window=1_000_000))
    agent = Agent(TestModel(), instructions=lambda: instructions[0], capabilities=[capability])

    first = asyncio.run(agent.run('first', message_history=history(30))).all_messages()
    assert not recorded_state(first)[0].compacted_calls
    resumed = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(first))
    same = asyncio.run(agent.run('same', message_history=resumed)).all_messages()
    assert not recorded_state(same)[0].compacted_calls

    instructions[0] = 'changed'
    changed = asyncio.run(agent.run('changed', message_history=same)).all_messages()
    state, events = recorded_state(changed)
    assert state.compacted_calls == tuple(f'c{i}' for i in range(10))
    assert len(state.dropped_parts) == 25
    assert len(events) == 1
    assert state.summary is None
    assert capability.last_view is not None and capability.last_view.summary_request is None

    again = asyncio.run(agent.run('again', message_history=changed)).all_messages()
    assert recorded_state(again)[1] == events

    rolled_back = asyncio.run(agent.run('rollback', message_history=first)).all_messages()
    assert recorded_state(rolled_back)[0].compacted_calls == tuple(f'c{i}' for i in range(10))


def test_instruction_change_during_tool_exchange_compacts_before_next_request() -> None:
    instructions = ['initial']
    requests = []

    def respond(messages, info):
        requests.append(messages)
        if any(isinstance(part, ToolReturnPart) and part.tool_name == 'change' for part in messages[-1].parts):
            return ModelResponse(parts=[TextPart(content='done')])
        return ModelResponse(parts=[ToolCallPart(tool_name='change', args={}, tool_call_id='new')])

    capability = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter(window=1_000_000))
    agent = Agent(FunctionModel(respond), instructions=lambda: instructions[0], capabilities=[capability])

    @agent.tool_plain
    def change() -> str:
        instructions[0] = 'changed'
        return 'ok'

    result = asyncio.run(agent.run('continue', message_history=history(30))).all_messages()
    state, events = recorded_state(result)
    assert state.compacted_calls == tuple(f'c{i}' for i in range(11))
    assert len(state.dropped_parts) == 25
    assert len(events) == 1
    assert len(requests) == 2
    assert state.summary is None
