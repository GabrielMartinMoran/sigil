"""Budget policy accounts for provider measurements, output and uncertainty once."""

import asyncio
from dataclasses import replace

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RequestUsage

from src.engine.bootstrap import build_strategy
from src.engine.context.budget import ContextBudget
from src.engine.context.capability import ContextBudgetExceeded, ContextViewCapability
from src.engine.context.compaction.events import RECORD_KEY
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.strategy.append_only import AppendOnlyStrategy
from src.engine.settings import load_settings


def test_should_expose_independent_retention_counts_and_safety_margin() -> None:
    settings = load_settings(
        {
            'SIGIL_CONTEXT_RECENT_REASONING_BLOCKS': '3',
            'SIGIL_CONTEXT_RECENT_TOOL_CALLS': '8',
            'SIGIL_CONTEXT_SAFETY_MARGIN_PERCENT': '7',
        }
    )
    strategy = build_strategy(settings)
    assert strategy.policy.recent_reasoning_blocks == 3
    assert strategy.policy.recent_tool_calls == 8
    assert settings.context_safety_margin_percent == 7


def test_should_subtract_safety_margin_and_output_only_once() -> None:
    budget = ContextBudget(window=10_000, tools=1000, instructions=500, output_reserve=2000, safety_margin_percent=5)
    assert budget.available_for_history == 6000
    assert budget.with_measurements(tools=1500).available_for_history == 5500
    assert budget.fits(6000)
    assert not budget.fits(6001)


def test_should_refuse_unknown_window_before_sending_append_only_request() -> None:
    capability = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter())
    with pytest.raises(ContextBudgetExceeded, match='unknown'):
        asyncio.run(Agent(TestModel(), capabilities=[capability]).run('work'))
    assert 'window_unknown' in capability.last_view.plan.exceptions


def test_should_use_measured_input_and_new_output_when_prefix_matches() -> None:
    from src.engine.context.input_estimate import estimate_input, history_fingerprint

    prefix = [ModelRequest(parts=[UserPromptPart('work')])]
    record = {'input_fingerprint': history_fingerprint(prefix), 'fixed_tokens': 200}
    response = ModelResponse(
        parts=[TextPart('ok')],
        model_name='same',
        usage=RequestUsage(input_tokens=5000, output_tokens=3000),
        metadata={RECORD_KEY: record},
    )
    working = [*prefix, response, ModelRequest(parts=[UserPromptPart('next')])]
    budget = ContextBudget(window=10000, instructions=400, output_reserve=1000, model_name='same')
    measured = estimate_input(working, budget)
    assert measured.source == 'provider_plus_delta'
    assert measured.total >= 8200
    changed = [replace(prefix[0], parts=[UserPromptPart('changed')]), *working[1:]]
    assert estimate_input(changed, budget).source == 'local'
    assert estimate_input(working, replace(budget, model_name='other')).source == 'local'


@pytest.mark.parametrize(
    'values',
    [
        {'SIGIL_CONTEXT_RECENT_TOOL_CALLS': '-1'},
        {'SIGIL_CONTEXT_RECENT_REASONING_BLOCKS': '-1'},
        {'SIGIL_CONTEXT_SAFETY_MARGIN_PERCENT': '-1'},
        {'SIGIL_CONTEXT_SAFETY_MARGIN_PERCENT': '100'},
    ],
)
def test_should_reject_invalid_safety_configuration(values: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        load_settings(values)


def test_should_reserve_reported_output_for_opaque_reasoning_after_compaction() -> None:
    from pydantic_ai.messages import ThinkingPart

    from src.engine.context.input_estimate import estimate_input

    response = ModelResponse(
        parts=[ThinkingPart(content='', signature='encrypted', provider_name='openai'), TextPart('answer')],
        usage=RequestUsage(input_tokens=5000, output_tokens=3000),
    )
    estimate = estimate_input([response], ContextBudget(window=10000))
    assert estimate.total >= 3000
    assert estimate.opaque_reserve > 0
