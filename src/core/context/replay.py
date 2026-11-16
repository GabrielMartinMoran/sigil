"""Offline deterministic-policy replay, not a provider cache or quality benchmark."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from pydantic_ai.messages import ModelMessage, ModelResponse

from src.core.context.compaction.compaction_event import POLICY_VERSION
from src.core.context.compaction.compaction_policy import CompactionPolicy
from src.core.context.compaction.events import RECORD_KEY
from src.core.context.context_budget import ContextBudget
from src.core.context.replay_report import ReplayReport
from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy
from src.core.context.tokens import part_tokens
from src.core.tooling.compaction_policies import ALL_COMPACTORS


def _prefix_tokens(previous: Sequence[ModelMessage], current: Sequence[ModelMessage]) -> int:
    before = [(message.kind, part) for message in previous for part in message.parts]
    after = [(message.kind, part) for message in current for part in message.parts]
    tokens = 0
    for old, new in zip(before, after):
        if old != new:
            break
        tokens += part_tokens(new[1])
    return tokens


def replay(messages: Sequence[ModelMessage], budget: ContextBudget, policy: CompactionPolicy) -> ReplayReport:
    """Replay until unsafe; never generate or fabricate a checkpoint.

    Prefix estimates cover matching complete parts and assume constant fixed blocks.
    They omit provider serialization, cache TTL/routing, cache-write pricing and how a
    different projection would change future model actions. Pending summaries stay
    explicit; a blocked request ends the replay instead of simulating impossible work.
    """
    strategy = AppendOnlyStrategy(policy=policy, lookup=ALL_COMPACTORS.get)
    history: list[ModelMessage] = []
    previous: list[ModelMessage] = []
    requests = input_tokens = common = compactions = summaries = 0
    blocked = False
    for message in messages:
        metadata = {key: value for key, value in (message.metadata or {}).items() if key != RECORD_KEY}
        clean = replace(message, metadata=metadata or None)
        if not isinstance(clean, ModelResponse):
            history.append(clean)
            continue
        view = strategy.view(history, budget)
        requests += 1
        assert view.plan is not None
        events = view.events[view.plan.recorded_events :]
        compactions += len(events)
        summaries += view.summary_request is not None
        blocked = view.budget_exceeded or budget.window is None
        if blocked:
            break
        input_tokens += budget.fixed + view.estimated_tokens
        common += _prefix_tokens(previous, view.messages) + (budget.fixed if requests > 1 else 0)
        record = {'version': POLICY_VERSION, 'state': view.plan.state.as_row(), 'events': [e.as_row() for e in events]}
        history.append(replace(clean, metadata={**metadata, RECORD_KEY: record}))
        previous = view.messages
    return ReplayReport(requests, input_tokens, common, compactions, summaries, blocked)
