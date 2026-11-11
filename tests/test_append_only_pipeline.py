"""The append-only pipeline: append, one deterministic batch, then a checkpoint."""

from __future__ import annotations

import asyncio
import dataclasses
import json

from pydantic_ai import Agent
from pydantic_ai.messages import (
    CompactionPart,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from src.engine.context.budget import ContextBudget
from src.engine.context.capability import ContextViewCapability
from src.engine.context.compaction.events import POLICY_VERSION, RECORD_KEY
from src.engine.context.compaction.policy import CompactionPolicy
from src.engine.context.compaction.summary import Summary
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from src.engine.context.tokens import estimate_messages_tokens
from src.engine.tooling.compaction_policies import ALL_COMPACTORS

PAYLOAD = 'x' * 4000


def turn(index: int) -> list:
    return [
        ModelResponse(
            parts=[
                ThinkingPart(content=f'thought {index}'),
                ToolCallPart(tool_name='read_file', args={'path': f'f{index}.py'}, tool_call_id=f'c{index}'),
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name='read_file',
                    content={'path': f'f{index}.py', 'content': PAYLOAD},
                    tool_call_id=f'c{index}',
                )
            ]
        ),
    ]


def conversation(turns: int) -> list:
    messages: list = [ModelRequest(parts=[UserPromptPart(content='go')])]
    for index in range(turns):
        messages += turn(index)
    return messages


def budget_for(messages: list, *, share: float) -> ContextBudget:
    """A window where the history lands on ``share`` of it."""
    return ContextBudget(window=int(estimate_messages_tokens(messages) / share) + 1, output_reserve=0)


def parts_of(messages) -> list:
    return [part for message in messages for part in message.parts]


def test_ordinary_operation_appends_without_rewriting_anything():
    messages = conversation(3)
    result = AppendOnlyStrategy(lookup=ALL_COMPACTORS.get).view(messages, budget_for(messages, share=0.2))

    assert [type(part) for part in parts_of(result.messages)] == [type(part) for part in parts_of(messages)]
    assert result.plan is not None and result.plan.state.compacted_calls == ()
    assert result.events == ()
    assert result.summary_request is None
    assert result.compacted_parts == 0


def test_crossing_the_trigger_reduces_old_payloads_and_reasoning_in_one_batch():
    messages = conversation(30)
    result = AppendOnlyStrategy(lookup=ALL_COMPACTORS.get).view(messages, budget_for(messages, share=0.9))

    state = result.plan.state
    assert state.compacted_calls == tuple(f'c{i}' for i in range(10))
    assert len(state.dropped_parts) == 25
    kept = [part for part in parts_of(result.messages) if isinstance(part, ThinkingPart)]
    assert len(kept) == 5
    # The payload of a compacted call is reduced, and the file path survives.
    reduced = [
        part for part in parts_of(result.messages) if isinstance(part, ToolReturnPart) and part.tool_call_id == 'c0'
    ]
    assert len(json.dumps(reduced[0].content)) < len(json.dumps(messages[2].parts[0].content))
    assert reduced[0].content['path'] == 'f0.py'
    assert result.summary_request is not None


def test_default_thresholds_trigger_compaction_and_request_a_summary_only_if_needed():
    strategy = AppendOnlyStrategy()
    below_trigger = conversation(30)
    below = strategy.view(below_trigger, budget_for(below_trigger, share=0.59))
    assert not below.plan.state.compacted_calls
    assert below.summary_request is None

    above_trigger = conversation(30)
    above_budget = budget_for(above_trigger, share=0.61)
    above = strategy.view(above_trigger, above_budget)
    assert above.plan.state.compacted_calls
    assert 0.4 < estimate_messages_tokens(above.messages) / above_budget.window < 0.6
    assert above.summary_request is not None

    enough_reduction = conversation(60)
    reduced_budget = budget_for(enough_reduction, share=0.61)
    reduced = strategy.view(enough_reduction, reduced_budget)
    assert reduced.plan.state.compacted_calls
    assert estimate_messages_tokens(reduced.messages) / reduced_budget.window < 0.4
    assert reduced.summary_request is None


def test_the_checkpoint_replaces_the_older_span_and_keeps_the_tail():
    messages = conversation(30)
    strategy = AppendOnlyStrategy(lookup=ALL_COMPACTORS.get)
    budget = budget_for(messages, share=0.9)
    request = strategy.view(messages, budget).summary_request
    assert request is not None

    applied = strategy.view(messages, budget, summary=Summary(text='## Goal\nkeep going', boundary=request.boundary))

    assert isinstance(applied.messages[0].parts[0], CompactionPart)
    assert applied.messages[0].parts[0].content == '## Goal\nkeep going'
    assert applied.plan.state.summary == '## Goal\nkeep going'
    assert applied.summary_request is None
    # The protected tail travels whole, and the transcript is untouched.
    assert applied.messages[-2:] == messages[-2:]
    assert len(messages) == 61


def test_the_checkpoint_covers_the_older_span_and_never_splits_an_exchange():
    messages = conversation(30)
    request = AppendOnlyStrategy().view(messages, budget_for(messages, share=0.9)).summary_request

    assert request is not None
    # Ten calls are compacted and twenty protected; the summary stops before them.
    assert len(request.messages) == 1 + 10 * 2
    # Every exchange the summary covers is complete: a call never travels without its
    # result, which is what a provider rejects.
    calls = {part.tool_call_id for part in parts_of(request.messages) if isinstance(part, ToolCallPart)}
    returns = {part.tool_call_id for part in parts_of(request.messages) if isinstance(part, ToolReturnPart)}
    assert calls == returns == {f'c{i}' for i in range(10)}
    assert request.previous is None


def test_a_checkpoint_is_not_requested_when_the_span_would_not_advance():
    messages = conversation(4)
    budget = budget_for(messages, share=0.9)
    strategy = AppendOnlyStrategy()
    first = strategy.view(messages, budget).summary_request
    assert first is not None

    applied = strategy.view(messages, budget, summary=Summary(text='done', boundary=first.boundary))
    assert applied.summary_request is None


def test_the_trigger_is_configurable_and_the_target_decides_the_summary():
    messages = conversation(30)
    budget = budget_for(messages, share=0.9)
    wide = CompactionPolicy(trigger_percent=95, target_percent=90)

    assert AppendOnlyStrategy(policy=wide).view(messages, budget).plan.state.compacted_calls == ()
    assert AppendOnlyStrategy().view(messages, budget).plan.state.compacted_calls


def test_a_previous_checkpoint_is_offered_to_the_next_summary():
    messages = conversation(30)
    budget = budget_for(messages, share=0.9)
    strategy = AppendOnlyStrategy()
    first = strategy.view(messages, budget).summary_request
    assert first is not None
    applied = strategy.view(messages, budget, summary=Summary(text='older checkpoint', boundary=first.boundary))

    record = {
        'version': POLICY_VERSION,
        'state': applied.plan.state.as_row(),
        'events': [event.as_row() for event in applied.events],
    }
    # Twenty new calls move the protected tail, so a new span exists to summarize and
    # the stored checkpoint has to be merged into it.
    grown = [*messages, *(message for index in range(30, 50) for message in turn(index))]
    grown[-1] = dataclasses.replace(grown[-1], metadata={RECORD_KEY: record})

    # An eager target makes the follow-up checkpoint certain, whatever the sizes are.
    eager = CompactionPolicy(trigger_percent=2, target_percent=1)
    again = AppendOnlyStrategy(policy=eager).view(grown, budget_for(grown, share=0.9))
    assert again.summary_request is not None
    assert again.summary_request.previous == 'older checkpoint'


def test_a_version_two_record_becomes_call_and_part_identities():
    messages = conversation(3)
    record = {
        'version': 2,
        'state': {
            'reasoning_cutoff': 1,
            'lod_cutoffs': [1, 0, 0],
            'dropped_parts': [],
            'evaluated_steps': 3,
            'compacted_calls': [],
        },
        'events': [],
    }
    messages[-1] = dataclasses.replace(messages[-1], metadata={RECORD_KEY: record})

    result = AppendOnlyStrategy(lookup=ALL_COMPACTORS.get).view(messages, ContextBudget(window=None))

    state = result.plan.state
    assert 'c0' in state.compacted_calls
    assert 'c1' not in state.compacted_calls
    assert any(key.startswith('thinking:') for key in state.dropped_parts)
    # Reading a legacy record does not reinterpret the payloads it described.
    assert len(messages) == 7


def test_the_capability_asks_the_model_for_the_checkpoint_and_applies_it():
    def answer(messages, info):
        if '<conversation>' in str(messages[-1].parts[0].content):
            return ModelResponse(parts=[TextPart('## Goal\ncontinue the work')])
        return ModelResponse(parts=[TextPart('ok')])

    history = conversation(30)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(lookup=ALL_COMPACTORS.get),
        meter=ContextMeter(window=budget_for(history, share=0.9).window, output_reserve=0),
    )
    agent = Agent(FunctionModel(answer), capabilities=[capability])
    asyncio.run(agent.run('continue', message_history=history))

    assert capability.summary_error is None
    assert capability.last_view is not None
    assert capability.last_view.plan.state.summary == '## Goal\ncontinue the work'


def test_a_failed_checkpoint_leaves_the_deterministic_projection_and_says_why():
    def broken(messages, info):
        if '<conversation>' in str(messages[-1].parts[0].content):
            raise RuntimeError('no summary today')
        return ModelResponse(parts=[TextPart('ok')])

    history = conversation(30)
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(lookup=ALL_COMPACTORS.get),
        meter=ContextMeter(window=budget_for(history, share=0.9).window, output_reserve=0),
    )
    agent = Agent(FunctionModel(broken), capabilities=[capability])
    asyncio.run(agent.run('continue', message_history=history))

    view = capability.last_view
    assert capability.summary_error == 'RuntimeError'
    assert view.plan.state.summary is None
    assert 'summary_failed' in view.plan.exceptions
    assert view.summary_request is None
    assert view.plan.state.compacted_calls


def test_a_recorded_decision_is_replayed_instead_of_recomputed():
    messages = conversation(3)
    decision = AppendOnlyStrategy().view(messages, budget_for(messages, share=0.9))
    record = {
        'version': POLICY_VERSION,
        'state': decision.plan.state.as_row(),
        'events': [event.as_row() for event in decision.events],
    }
    messages.append(ModelResponse(parts=[TextPart('later')], metadata={RECORD_KEY: record}))

    replay = AppendOnlyStrategy().view(messages, ContextBudget(window=None))

    assert replay.plan.state.compacted_calls == decision.plan.state.compacted_calls
    assert replay.events == decision.events
