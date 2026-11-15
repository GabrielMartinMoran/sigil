"""What a session has cost: where the number comes from, and when it is a floor.

The tests pin the *provenance*, not the price table: the amounts a provider
reports are exact and must win, the ones a table produces are estimates, and a
request nothing can price must not be silently counted as zero.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage

from src.engine.context.meter.cost import format_usd, session_cost

# --- helpers ---------------------------------------------------------------


def _response(
    *,
    model: str | None = 'claude-sonnet-4-5',
    provider_name: str | None = None,
    provider_url: str | None = None,
    provider_details: dict | None = None,
    **usage,
) -> ModelResponse:
    return ModelResponse(
        parts=[TextPart(content='ok')],
        usage=RequestUsage(**usage),
        model_name=model,
        provider_name=provider_name,
        provider_url=provider_url,
        provider_details=provider_details,
    )


def _request() -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content='hello')])


# --- provenance ------------------------------------------------------------


def test_a_session_that_sent_nothing_costs_zero():
    """Not `?`: nothing went out, so there is nothing unknown about the bill."""
    cost = session_cost([])
    assert cost.total == Decimal(0)
    assert cost.requests == 0
    assert not cost.partial
    assert not cost.approximate


def test_the_provider_reported_cost_wins_over_the_list_price():
    """OpenRouter charges the number it sends, and pydantic-ai keeps it aside.

    `usage.cost` is the list-price estimate pydantic-ai computes for every
    response; the reported charge travels in `provider_details` and is the invoice.
    """
    response = _response(
        provider_name='openrouter',
        provider_url='https://openrouter.ai/api/v1',
        input_tokens=1_000,
        output_tokens=100,
        cost=Decimal('0.0375'),
        provider_details={'cost': 0.012, 'downstream_provider': 'anthropic'},
    )

    cost = session_cost([response])

    assert cost.total == Decimal('0.012')
    assert cost.reported == 1
    assert cost.estimated == 0
    assert cost.exact
    assert not cost.approximate


def test_the_list_price_is_summed_and_marked_as_an_estimate():
    messages = [
        _response(input_tokens=100, output_tokens=10, cost=Decimal('0.0010')),
        _response(input_tokens=100, output_tokens=10, cost=Decimal('0.0020')),
    ]

    cost = session_cost(messages)

    assert cost.total == Decimal('0.003')
    assert cost.estimated == 2
    assert cost.reported == 0
    assert cost.approximate
    assert not cost.partial


def test_a_model_no_table_knows_is_unpriced_instead_of_free():
    response = _response(
        model='a-model-from-next-year',
        provider_name='nowhere',
        input_tokens=100,
        output_tokens=10,
    )

    cost = session_cost([response])

    assert cost.total is None
    assert cost.unpriced == 1
    assert cost.partial


def test_a_provider_the_table_cannot_name_is_priced_at_the_vendor_rate():
    """OpenCode serves the vendored models under its own name.

    `genai-prices` has no `opencode` provider, so pydantic-ai leaves `usage.cost`
    empty; the model id still resolves to the vendor that publishes it, and that
    is a better answer than `?` — an estimate, and a conservative one.
    """
    response = _response(
        model='claude-sonnet-4-5',
        provider_name='opencode',
        provider_url='https://opencode.ai/inference/openai/v1',
        input_tokens=1_000_000,
        output_tokens=1_000,
    )

    cost = session_cost([response])

    assert cost.total is not None
    assert cost.total > 0
    assert cost.estimated == 1
    assert cost.approximate


def test_a_response_without_usage_is_counted_apart_from_an_unpriced_one():
    """Omitted usage and an unknown price are two different holes in the total."""
    response = _response(model='claude-sonnet-4-5', unmeasured_requests=1)

    cost = session_cost([response])

    assert cost.total is None
    assert cost.unmeasured == 1
    assert cost.unpriced == 0
    assert cost.partial


def test_a_partly_priced_session_reports_the_known_amount_as_a_floor():
    messages = [
        _response(input_tokens=100, output_tokens=10, cost=Decimal('0.0010')),
        _response(model='a-model-from-next-year', provider_name='nowhere', input_tokens=100, output_tokens=10),
    ]

    cost = session_cost(messages)

    assert cost.total == Decimal('0.0010')
    assert cost.estimated == 1
    assert cost.unpriced == 1
    assert cost.partial


def test_only_responses_are_priced():
    """A request carries no usage: the prompt is billed as part of the answer."""
    messages = [_request(), _response(input_tokens=100, output_tokens=10, cost=Decimal('0.0010'))]

    cost = session_cost(messages)

    assert cost.requests == 1
    assert cost.total == Decimal('0.0010')


def test_a_reloaded_session_keeps_its_bill():
    """`/load` rebuilds the history from JSON, so the amounts must survive the trip.

    Both halves matter: the `Decimal` a reported cost arrives as, and the
    `provider_details` block it hides in, which is the only place the charge is.
    """
    response = _response(
        provider_name='openrouter',
        input_tokens=100,
        output_tokens=10,
        cost=Decimal('0.0375'),
        provider_details={'cost': 0.012, 'downstream_provider': 'anthropic'},
    )

    reloaded = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json([response]))
    cost = session_cost(reloaded)

    assert cost.total == Decimal('0.012')
    assert cost.reported == 1


def test_the_counts_add_up_to_the_requests_that_were_sent():
    messages = [
        _response(input_tokens=100, output_tokens=10, cost=Decimal('0.0010')),
        _response(provider_name='openrouter', input_tokens=100, output_tokens=10, provider_details={'cost': 0.5}),
        _response(model='a-model-from-next-year', provider_name='nowhere', input_tokens=1, output_tokens=1),
        _response(unmeasured_requests=1),
    ]

    cost = session_cost(messages)

    assert cost.requests == 4
    assert cost.reported == 1
    assert cost.estimated == 1
    assert cost.unpriced == 1
    assert cost.unmeasured == 1
    assert cost.total == Decimal('0.5010')


# --- the price lookup table ------------------------------------------------


def test_the_vendor_price_is_computed_once_per_distinct_usage(monkeypatch):
    """The panel recomputes the bill at every checkpoint, so a price is asked once.

    Editing the module attribute is the point: it is the seam a refactor would
    remove without any other test noticing.
    """
    import src.engine.context.meter.cost as cost_module

    real = cost_module.calc_price
    calls: list[str] = []

    def counting(usage, model_ref, **kwargs):
        calls.append(model_ref)
        return real(usage, model_ref, **kwargs)

    monkeypatch.setattr(cost_module, 'calc_price', counting)
    monkeypatch.setattr(cost_module, '_PRICE_CACHE', {})

    response = _response(model='claude-sonnet-4-5', provider_name='opencode', input_tokens=1_000, output_tokens=100)
    session_cost([response, response])
    session_cost([response])

    assert calls == ['claude-sonnet-4-5']


def test_different_usage_of_the_same_model_is_not_collapsed():
    messages = [
        _response(model='claude-sonnet-4-5', provider_name='opencode', input_tokens=1_000_000, output_tokens=0),
        _response(model='claude-sonnet-4-5', provider_name='opencode', input_tokens=2_000_000, output_tokens=0),
    ]

    cost = session_cost(messages)

    small, medium = (session_cost([message]).total for message in messages)
    assert small is not None and medium is not None
    assert cost.total == small + medium
    assert medium > small


# --- formatting ------------------------------------------------------------


def test_usd_keeps_four_decimals():
    """A single request can cost a fraction of a cent, and `/context` shows it."""
    assert format_usd(Decimal('0.0432')) == '$0.0432'
    assert format_usd(Decimal('12.5')) == '$12.5000'


def test_usd_shows_a_floor_instead_of_a_fake_zero():
    assert format_usd(Decimal('0.00004')) == '<$0.0001'


def test_usd_of_zero_is_zero():
    assert format_usd(Decimal(0)) == '$0'


def test_usd_unknown_is_a_question_mark():
    assert format_usd(None) == '?'
