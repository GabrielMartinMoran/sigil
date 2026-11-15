"""Projection estimates are reused locally, never across views or sessions."""

from __future__ import annotations

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage

from src.engine.context import input_estimate
from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.events import POLICY_VERSION, RECORD_KEY
from src.engine.context.strategy.append_only import AppendOnlyStrategy


def measured_history() -> list[ModelMessage]:
    prefix = [ModelRequest([UserPromptPart('inspect')])]
    response = ModelResponse(
        [TextPart('done')],
        model_name='test',
        usage=RequestUsage(input_tokens=1000, output_tokens=10),
        metadata={
            RECORD_KEY: {
                'version': POLICY_VERSION,
                'state': {},
                'events': [],
                'input_fingerprint': input_estimate.history_fingerprint(prefix),
                'fixed_tokens': 0,
            }
        },
    )
    return [*prefix, response, ModelRequest([UserPromptPart('next')])]


def test_should_fingerprint_unchanged_projection_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = measured_history()
    calls: list[int] = []
    fingerprint = input_estimate.history_fingerprint

    def counted(sequence: Sequence[ModelMessage]) -> str:
        calls.append(len(sequence))
        return fingerprint(sequence)

    monkeypatch.setattr(input_estimate, 'history_fingerprint', counted)
    view = AppendOnlyStrategy().view(messages, ContextBudget(window=100_000, model_name='test'))
    assert view.diagnostics['input_estimate_source'] == 'provider_plus_delta'
    assert calls == [1]


def test_should_recompute_after_in_place_history_change() -> None:
    messages = measured_history()
    strategy = AppendOnlyStrategy()
    budget = ContextBudget(window=100_000, model_name='test')
    first = strategy.view(messages, budget)
    messages[0].parts[0].content = 'changed prefix'
    second = strategy.view(messages, budget)
    assert first.diagnostics['input_estimate_source'] == 'provider_plus_delta'
    assert second.diagnostics['input_estimate_source'] == 'local'


def test_should_isolate_concurrent_views_and_changed_budgets() -> None:
    strategy = AppendOnlyStrategy()
    budget = ContextBudget(window=100_000, model_name='test')
    histories = [measured_history(), measured_history()]
    histories[1][0] = ModelRequest([UserPromptPart('different session')])
    budgets = [budget, replace(budget, instructions=100, model_name='other')]
    expected = [strategy.view(messages, current) for messages, current in zip(histories, budgets, strict=True)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        actual = list(executor.map(strategy.view, histories, budgets))
    assert actual == expected


@pytest.mark.parametrize('window', [None, 500, 3000, 100_000])
@pytest.mark.parametrize('opportunistic', [False, True])
def test_should_preserve_pressure_decisions_without_reusing_stale_estimates(
    window: int | None, opportunistic: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.messages import ThinkingPart, ToolCallPart, ToolReturnPart

    from src.engine.context.compaction.measurements import ProjectionMeasurements
    from src.engine.context.input_estimate import InputEstimate
    from src.engine.context.strategy import append_only
    from src.engine.context.tokens import estimate_messages_tokens

    class UncachedMeasurements(ProjectionMeasurements):
        def tokens(self, messages: Sequence[ModelMessage]) -> int:
            return estimate_messages_tokens(messages)

        def input(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> InputEstimate:
            return input_estimate.estimate_input(messages, budget)

    messages = measured_history()
    for step in range(12):
        messages.extend(
            [
                ModelResponse(
                    [ThinkingPart('reasoning ' * 100), ToolCallPart('run', {'cmds': ['echo ' * 100]}, str(step))]
                ),
                ModelRequest([ToolReturnPart('run', 'output ' * 1000, str(step))]),
            ]
        )
    strategy = AppendOnlyStrategy()
    budget = ContextBudget(window=window, model_name='test', output_reserve=100)
    actual = strategy.view(messages, budget, compact_opportunistically=opportunistic)
    monkeypatch.setattr(append_only, 'ProjectionMeasurements', UncachedMeasurements)
    expected = strategy.view(messages, budget, compact_opportunistically=opportunistic)
    assert actual == expected
