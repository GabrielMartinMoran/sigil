"""Maintenance records survive real agent hooks and the transactional session store."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from src.engine.context.budget import ContextBudget
from src.engine.context.capability import ContextBudgetExceeded, ContextViewCapability
from src.engine.context.compaction.events import RECORD_KEY
from src.engine.context.compaction.records import ContextPolicyVersionError, recorded_state
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from src.engine.context.tokens import estimate_messages_tokens
from src.engine.sessions.session_store import SessionStore


def history(count: int) -> list:
    messages = [ModelRequest(parts=[UserPromptPart(content='go')], run_id='past')]
    for i in range(count):
        messages.extend(
            [
                ModelResponse(
                    parts=[
                        ThinkingPart(content=f'thought {i}'),
                        ToolCallPart(tool_name='noop', args={}, tool_call_id=f'c{i}'),
                    ],
                    run_id='past',
                ),
                ModelRequest(
                    parts=[ToolReturnPart(tool_name='noop', content='ok', tool_call_id=f'c{i}')], run_id='past'
                ),
            ]
        )
    return messages


def pressured_window(messages: list) -> int:
    """A window the history crosses at the trigger while still fitting the hard budget."""
    return int(estimate_messages_tokens(messages) * 1.1) + 1


def run_recorded(messages: list, *, window: int | None = None) -> list:
    window = pressured_window(messages) if window is None else window
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(), meter=ContextMeter(window=window, output_reserve=0)
    )
    agent = Agent(TestModel(), capabilities=[capability])
    return asyncio.run(agent.run('continue', message_history=messages)).all_messages()


def test_agent_hook_persists_its_decision_without_altering_original_payloads():
    messages = history(30)
    original = ModelMessagesTypeAdapter.dump_json(messages)
    result = run_recorded(messages)
    state, events = recorded_state(result)
    # 30 calls, the newest 20 protected: the first ten are compacted.
    assert state.compacted_calls == tuple(f'c{i}' for i in range(10))
    assert [event.step for event in events] == [30]
    assert any(RECORD_KEY in (message.metadata or {}) for message in result)
    assert ModelMessagesTypeAdapter.dump_json(messages) == original
    assert sum(isinstance(p, ThinkingPart) for m in result for p in m.parts) == 30


def test_recorded_boundaries_survive_multiple_sqlite_resumes_and_config_changes(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'home')
    session = store.create_session()
    session.messages = run_recorded(history(30))
    store.save(session)
    before = recorded_state(session.messages)
    assert before[0].compacted_calls
    for _ in range(2):
        session = store.load(session.id)
        assert recorded_state(session.messages) == before
        session.messages = run_recorded(session.messages, window=100_000)
        store.save(session)
        state, events = recorded_state(session.messages)
        assert state.compacted_calls == before[0].compacted_calls
        assert events == before[1]
        before = state, events


def test_rollback_and_alternative_branch_cannot_inherit_future_events():
    original = history(30)
    completed = run_recorded(original)
    assert recorded_state(completed)[1]
    rolled_back = completed[: len(original)]
    assert not recorded_state(rolled_back)[1]
    alternative = run_recorded(rolled_back[:5], window=100_000)
    assert recorded_state(alternative)[0].compacted_calls == ()
    assert not recorded_state(alternative)[1]


def test_interleaved_agents_do_not_share_state():
    window = pressured_window(history(30))
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(), meter=ContextMeter(window=window, output_reserve=0)
    )
    agent = Agent(TestModel(), capabilities=[capability])

    async def run_both():
        return await asyncio.gather(
            agent.run('a', message_history=history(30)), agent.run('b', message_history=history(2))
        )

    first, second = asyncio.run(run_both())
    assert recorded_state(first.all_messages())[0].compacted_calls
    assert recorded_state(second.all_messages())[0].compacted_calls == ()


def test_debug_projection_is_pure_and_retains_recorded_events():
    messages = run_recorded(history(30))
    original = ModelMessagesTypeAdapter.dump_json(messages)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(), meter=ContextMeter(window=pressured_window(history(30)), output_reserve=0)
    )
    first = capability.project(messages)
    second = capability.project(messages)
    assert first.events == second.events == recorded_state(messages)[1]
    assert first.messages == second.messages
    assert ModelMessagesTypeAdapter.dump_json(messages) == original


def test_unknown_policy_version_fails_explicitly():
    messages = run_recorded(history(30))
    last_index = next(i for i, m in reversed(list(enumerate(messages))) if RECORD_KEY in (m.metadata or {}))
    last = messages[last_index]
    record = {**last.metadata[RECORD_KEY], 'version': 999}
    messages[last_index] = replace(last, metadata={RECORD_KEY: record})
    with pytest.raises(ContextPolicyVersionError, match='migration'):
        AppendOnlyStrategy().view(messages, ContextBudget(window=None))


def test_overfull_protected_request_never_reaches_the_model():
    capability = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter(window=100, output_reserve=0))
    agent = Agent(TestModel(), capabilities=[capability])
    with pytest.raises(ContextBudgetExceeded, match='larger context window'):
        asyncio.run(agent.run('x' * 10000))
    assert capability.last_view.budget_exceeded


def test_streamed_response_records_the_same_request_decision():
    messages = history(30)
    window = pressured_window(messages)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(), meter=ContextMeter(window=window, output_reserve=0)
    )
    agent = Agent(TestModel(), capabilities=[capability])

    async def streamed():
        async with agent.run_stream('continue', message_history=messages) as run:
            await run.get_output()
        return run.all_messages()

    recorded = asyncio.run(streamed())
    assert recorded_state(recorded)[0].compacted_calls == tuple(f'c{i}' for i in range(10))
    assert [event.step for event in recorded_state(recorded)[1]] == [30]


def test_failed_transcript_transaction_does_not_persist_half_an_event(tmp_path, monkeypatch):
    from src.engine.storage import executions

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'home')
    session = store.create_session()
    session.messages = history(19)
    store.save(session)
    before = ModelMessagesTypeAdapter.dump_json(store.load(session.id).messages)
    session.messages = run_recorded(history(30))
    original_write = executions.write_history

    def fail_after_write(*args, **kwargs):
        original_write(*args, **kwargs)
        raise RuntimeError('simulated disk failure')

    monkeypatch.setattr(executions, 'write_history', fail_after_write)
    with pytest.raises(RuntimeError, match='disk failure'):
        store.save(session)
    restored = store.load(session.id)
    assert ModelMessagesTypeAdapter.dump_json(restored.messages) == before
    assert not recorded_state(restored.messages)[1]


def test_a_failed_checkpoint_never_becomes_a_stored_decision():
    from pydantic_ai.models.function import FunctionModel

    def broken(messages, info):
        raise RuntimeError('summarization unavailable')

    from tests.test_append_only_pipeline import conversation

    messages = conversation(30)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(), meter=ContextMeter(window=pressured_window(messages), output_reserve=0)
    )
    agent = Agent(FunctionModel(broken), capabilities=[capability])

    with pytest.raises(RuntimeError, match='summarization unavailable'):
        asyncio.run(agent.run('continue', message_history=messages))
    assert capability.summary_error == 'RuntimeError'


def test_child_request_does_not_replace_root_debug_snapshot_or_budget():
    from types import SimpleNamespace

    from pydantic_ai import RunContext

    meter = ContextMeter(window=100000)
    capability = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=meter)
    agent = Agent(TestModel(), capabilities=[capability])

    @agent.instructions
    def instructions(ctx: RunContext) -> str:
        return 'child instructions ' * 100 if getattr(ctx.deps, 'node_id', None) else 'root instructions'

    asyncio.run(agent.run('root'))
    root_view, root_snapshot, root_budget = capability.last_view, capability.last_request, meter.budget()
    asyncio.run(agent.run('child', deps=SimpleNamespace(node_id='child')))
    assert capability.last_view is root_view
    assert capability.last_request is root_snapshot
    assert meter.budget() == root_budget


def test_failed_prepared_request_retains_its_events_when_the_tail_is_reprepared():
    from pydantic_ai.models.function import FunctionModel

    def offline(messages, info):
        raise RuntimeError('simulated offline provider')

    messages = history(30)
    window = pressured_window(messages)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(), meter=ContextMeter(window=window, output_reserve=0)
    )
    agent = Agent(FunctionModel(offline), capabilities=[capability])

    async def interrupted():
        async with agent.iter('continue', message_history=messages) as run:
            try:
                async for _ in run:
                    pass
            except RuntimeError:
                return run.all_messages()
        raise AssertionError('Expected a simulated failure')

    failed = asyncio.run(interrupted())
    state, events = recorded_state(failed)
    assert state.compacted_calls and events
    resumed = run_recorded(failed, window=100_000)
    assert recorded_state(resumed)[0].compacted_calls == state.compacted_calls
    assert recorded_state(resumed)[1] == events


def test_streamed_history_survives_two_resumes_without_reinterpreting_events():
    messages = history(30)
    window = pressured_window(messages)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(), meter=ContextMeter(window=window, output_reserve=0)
    )
    agent = Agent(TestModel(), capabilities=[capability])

    async def streamed():
        current = messages
        previous = None
        for _ in range(3):
            async with agent.run_stream('continue', message_history=current) as run:
                await run.get_output()
            current = run.all_messages()
            state, events = recorded_state(current)
            assert state.compacted_calls
            assert len(events) == 1
            assert events[0].policy['trigger_percent'] == 75
            assert events[0].window == window
            if previous is not None:
                assert events == previous
            previous = events
        return current

    recorded = asyncio.run(streamed())
    assert recorded_state(recorded)[1][0].step == 30


def test_debug_timeline_shows_real_compaction_on_request_that_recorded_it():
    from src.engine.context.meter.debug import build_debug_report
    from src.engine.context.meter.request import RequestSnapshot
    from src.ui.widgets.debug_view import _turn_timeline

    messages = history(30)
    window = pressured_window(messages)
    recorded = run_recorded(messages, window=window)
    meter = ContextMeter(window=window, output_reserve=0)
    view = AppendOnlyStrategy().view(recorded, meter.budget())
    report = build_debug_report(
        recorded, snapshot=meter.snapshot(recorded), request=RequestSnapshot(), view_result=view
    )
    assert report.turns[-1].events
    timeline = _turn_timeline(report).plain
    assert 'Step 1' in timeline
    assert 'compaction: deterministic' in timeline
