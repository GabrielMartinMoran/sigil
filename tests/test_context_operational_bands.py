"""Inclusive ceilings, coordinated targets, multiple blocks, and absence of churn."""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    NativeToolCallPart,
    NativeToolReturnPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.coordinator import apply, exceptions, plan
from src.engine.context.compaction.policy import CompactionPolicy
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.tooling.lod import ToolLOD
from tests.test_context_compaction_records import history


def test_reasoning_count_ceiling_is_inclusive_and_next_request_only_appends():
    strategy = WindowedStrategy()
    messages = history(21)
    # The transcript helper contributes one prompt plus two messages per model step.
    below = strategy.view(messages[:39], ContextBudget(window=None))
    at = strategy.view(messages[:41], ContextBudget(window=None))
    above = strategy.view(messages, ContextBudget(window=None))
    assert not below.events
    assert at.events[-1].step == 20
    assert at.diagnostics['reasoning_steps'] == 10
    assert above.diagnostics['reasoning_steps'] == 11
    assert above.messages[: len(at.messages)] == at.messages
    assert above.events == at.events


def test_size_ceiling_alone_reduces_to_both_applicable_targets():
    messages = [ModelResponse(parts=[ThinkingPart(content='x' * 800)]) for _ in range(5)]
    result = WindowedStrategy().view(messages, ContextBudget(window=10000, output_reserve=0))
    assert result.events[-1].step == 5  # 5 * 200 = 10% of the window
    assert result.diagnostics['reasoning_steps'] == 2
    assert result.diagnostics['reasoning_known_tokens'] <= 500


def test_multiple_reasoning_blocks_count_as_one_step_and_unknown_sizes_still_trigger():
    messages = [
        ModelResponse(parts=[ThinkingPart(content='', signature='opaque'), ThinkingPart(content='why')])
        for _ in range(20)
    ]
    result = WindowedStrategy().view(messages, ContextBudget(window=None))
    assert result.events[-1].step == 20
    assert result.diagnostics['reasoning_steps'] == 10
    assert result.diagnostics['reasoning_unknown_blocks'] == 10
    assert result.diagnostics['reasoning_size'] == 'partial'


def test_one_component_triggers_eligible_reductions_of_other_components():
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name='external', args={'q': 'x' * 3000}, tool_call_id='c')]),
        ModelRequest(parts=[ToolReturnPart(tool_name='external', content='y' * 3000, tool_call_id='c')]),
    ]
    messages += [ModelResponse(parts=[ThinkingPart(content='why')]) for _ in range(20)]
    policy = CompactionPolicy(lod_ages=(1, 50, 75), tool_trigger_chars=100000)
    decision = plan(messages, policy=policy)
    assert decision.events[-1].step == 21
    assert decision.level_of('c') is ToolLOD.LOD_1
    assert decision.events[-1].reasoning_kept == 10


def test_short_tool_results_do_not_create_no_progress_events():
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name='external', args={}, tool_call_id='c')]),
        ModelRequest(parts=[ToolReturnPart(tool_name='external', content='ok', tool_call_id='c')]),
    ]
    messages += [ModelResponse(parts=[TextPart(content='step')]) for _ in range(100)]
    result = WindowedStrategy().view(messages, ContextBudget(window=None))
    assert not result.events
    assert result.messages == messages


def test_native_provider_exchange_is_never_corrupted_and_exception_is_explicit():
    messages = [
        ModelResponse(
            parts=[NativeToolCallPart(tool_name='web_search', args={'q': 'x' * 5000}, tool_call_id='native')]
        ),
        ModelResponse(
            parts=[NativeToolReturnPart(tool_name='web_search', content={'result': 'x' * 5000}, tool_call_id='native')]
        ),
    ]
    decision = plan(messages, policy=CompactionPolicy(lod_ages=(0, 1, 2)), pressure=True)
    reduced, _ = apply(messages, decision)
    assert reduced == messages
    assert 'provider_native_exchange' in exceptions(messages, decision, None)


def test_event_counters_are_immutable():
    import pytest

    event = WindowedStrategy().view(history(20), ContextBudget(window=None)).events[-1]
    with pytest.raises(TypeError):
        event.detail['compacted_calls'] = 999


def test_eligible_but_unreducible_arguments_are_preserved_and_reported():
    messages = [ModelResponse(parts=[ToolCallPart(tool_name='foreign', args='{malformed', tool_call_id='bad')])]
    messages += [ModelResponse(parts=[TextPart(content='step')]) for _ in range(30)]
    view = WindowedStrategy().view(messages, ContextBudget(window=None))
    assert view.messages == messages
    assert 'malformed_arguments' in view.diagnostics['exceptions']


def test_eligible_but_not_yet_applied_safe_reduction_is_not_an_exception():
    messages = [
        ModelResponse(parts=[ToolCallPart(tool_name='foreign', args={}, tool_call_id='small')]),
        ModelRequest(parts=[ToolReturnPart(tool_name='foreign', content='x' * 2500, tool_call_id='small')]),
    ]
    messages += [ModelResponse(parts=[TextPart(content='step')]) for _ in range(30)]
    view = WindowedStrategy().view(messages, ContextBudget(window=None))
    assert not view.events
    assert view.diagnostics['exceptions'] == ()
    assert view.messages == messages


def test_fixed_instructions_alone_can_exceed_the_window_even_with_empty_history():
    budget = ContextBudget(window=100, instructions=200, output_reserve=0)
    assert budget.fits(0) is False
    view = WindowedStrategy().view([], budget)
    assert view.budget_exceeded
    assert 'window' in view.events[-1].unmet


def test_one_request_has_one_coordinated_event_even_when_budget_escalates():
    messages = history(20)
    result = WindowedStrategy(reasoning_max_percent=100).view(
        messages,
        ContextBudget(window=50, output_reserve=0),
    )
    current = [event for event in result.events if event.step == 20]
    assert len(current) == 1
    assert current[0].reason == 'emergency'
    assert current[0].reasoning_kept == result.diagnostics['reasoning_steps']
    assert current[0].detail['reasoning_after'] == result.diagnostics['reasoning_steps']
