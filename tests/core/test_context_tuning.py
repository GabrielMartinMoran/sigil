"""Tuning has one source, and a change reaches the real strategy and tool policies."""

from __future__ import annotations

import pytest

from src.core.build_strategy import build_strategy
from src.core.context.compaction.compaction_policy import DEFAULT_POLICY, CompactionPolicy
from src.core.context.context_budget import ContextBudget
from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy
from src.core.load_settings import load_settings
from src.core.tooling.compaction_constants import SEMANTIC_COMPACT_CHARS
from src.core.tooling.compaction_policies import ALL_COMPACTORS
from tests.support.context_compaction_records import history


def test_policy_defaults_match_the_documented_thresholds() -> None:
    policy = CompactionPolicy()
    assert policy.trigger_percent == 60
    assert policy.target_percent == 40
    settings = load_settings({})
    assert settings.context_trigger_percent == 60
    assert settings.context_target_percent == 40
    assert policy.recent_reasoning_blocks == 5
    assert policy.recent_tool_calls == 20
    assert DEFAULT_POLICY == policy


def test_the_default_strategy_is_append_only() -> None:
    assert isinstance(build_strategy(load_settings({})), AppendOnlyStrategy)


def test_passthrough_remains_available() -> None:
    from src.core.context.strategy.passthrough_strategy import PassthroughStrategy

    assert isinstance(build_strategy(load_settings({'SIGIL_CONTEXT_STRATEGY': 'passthrough'})), PassthroughStrategy)


def test_the_thresholds_reach_the_strategy() -> None:
    strategy = build_strategy(
        load_settings({'SIGIL_CONTEXT_TRIGGER_PERCENT': '85', 'SIGIL_CONTEXT_TARGET_PERCENT': '70'})
    )
    assert isinstance(strategy, AppendOnlyStrategy)
    assert strategy.policy.trigger_percent == 85
    assert strategy.policy.target_percent == 70


def test_a_semantic_budget_change_reaches_the_registered_tool_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(SEMANTIC_COMPACT_CHARS, 'process_input', 50)
    args = {'cmds': ['command ' * 1000]}
    reduced = ALL_COMPACTORS['run'].compact_input(args)
    assert len(reduced['cmds'][0]) <= 50


def test_an_unknown_window_never_triggers_compaction() -> None:
    strategy = AppendOnlyStrategy()
    result = strategy.view(history(12), ContextBudget(window=None))
    assert result.plan is not None
    assert result.plan.state.compacted_calls == ()
