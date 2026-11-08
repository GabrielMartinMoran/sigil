"""Tuning has one source, with changes reaching real policies and both strategies."""

from __future__ import annotations

from src.engine.bootstrap import build_strategy
from src.engine.context.budget import ContextBudget
from src.engine.context.compaction import constants
from src.engine.context.compaction.policy import CompactionPolicy
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.settings import load_settings
from src.engine.tooling.lod import GENERIC_LOD, LOD_TARGET_CHARS, ToolLOD
from src.engine.tooling.lod_constants import GENERIC_LOD_CHARS, SEMANTIC_LOD_CHARS
from src.engine.tooling.lod_policies import ALL_POLICIES
from tests.test_context_compaction_records import history


def test_policy_defaults_reference_central_thresholds():
    policy = CompactionPolicy()
    assert policy.reasoning_steps == constants.REASONING_TARGET_STEPS
    assert policy.reasoning_trigger_steps == constants.REASONING_CEILING_STEPS
    assert policy.reasoning_max_percent == constants.REASONING_TARGET_PERCENT
    assert policy.reasoning_trigger_percent == constants.REASONING_CEILING_PERCENT
    assert policy.lod_ages == constants.TOOL_LOD_AGES_STEPS
    assert policy.tool_trigger_chars == constants.TOOL_PENDING_REDUCTION_CEILING_CHARS
    assert policy.tool_target_chars == constants.TOOL_PENDING_REDUCTION_TARGET_CHARS
    assert tuple(LOD_TARGET_CHARS[level] for level in list(ToolLOD)[1:]) == GENERIC_LOD_CHARS


def test_changing_the_central_reasoning_ceiling_changes_both_strategy_triggers(monkeypatch):
    monkeypatch.setattr(constants, 'REASONING_CEILING_STEPS', 12)
    for mode in ('windowed', 'compacting'):
        strategy = build_strategy(load_settings({'SIGIL_CONTEXT_STRATEGY': mode}))
        result = strategy.view(history(12), ContextBudget(window=None))
        assert result.events[-1].step == 12
        assert result.events[-1].reasoning_kept == constants.REASONING_TARGET_STEPS


def test_changing_a_semantic_budget_changes_the_registered_tool_policy(monkeypatch):
    monkeypatch.setitem(SEMANTIC_LOD_CHARS, 'process_input', (200, 100, 50))
    args = {'cmds': ['command ' * 1000]}
    reduced = ALL_POLICIES['run'].compact_input(args, ToolLOD.LOD_3)
    assert len(reduced['cmds'][0]) <= 50
    assert 'truncated' in reduced['cmds'][0]


def test_generic_ladder_has_no_environment_override():
    strategy = build_strategy(load_settings({'SIGIL_CONTEXT_MAX_TOOL_RESULT_CHARS': '99999'}))
    assert isinstance(strategy, WindowedStrategy)
    value = 'x' * 20000
    for level, budget in zip(list(ToolLOD)[1:], GENERIC_LOD_CHARS):
        assert len(GENERIC_LOD.compact_output(value, level)) <= budget


def test_configurable_tool_target_stops_after_enough_eligible_reduction():
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart

    from src.engine.context.compaction.coordinator import plan

    messages = []
    for call_id in ('old', 'new'):
        messages.extend(
            [
                ModelResponse(parts=[ToolCallPart(tool_name='external', args={}, tool_call_id=call_id)]),
                ModelRequest(parts=[ToolReturnPart(tool_name='external', content='x' * 3000, tool_call_id=call_id)]),
            ]
        )
    messages.append(ModelResponse(parts=[TextPart(content='third step')]))
    policy = CompactionPolicy(lod_ages=(1, 10, 20), tool_trigger_chars=1800, tool_target_chars=1500)
    decision = plan(messages, policy=policy)
    assert decision.level_of('old') is ToolLOD.LOD_1
    assert decision.level_of('new') is ToolLOD.FULL


def test_invalid_bands_are_rejected_before_a_request_is_built():
    import pytest

    for overrides in (
        {'reasoning_steps': -1},
        {'reasoning_trigger_steps': 10},
        {'reasoning_max_percent': 11},
        {'reasoning_trigger_percent': 101},
        {'lod_ages': (10, 5, 20)},
        {'lod_ages': (1, 2)},
        {'lod_ages': (-1, 2, 3)},
        {'tool_target_chars': 4000},
    ):
        with pytest.raises(ValueError):
            CompactionPolicy(**overrides)
