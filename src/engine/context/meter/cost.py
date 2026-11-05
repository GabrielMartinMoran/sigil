"""What a session has cost so far, in dollars.

The price of a request can come from three places, and the difference matters
enough to travel with the number:

1. **The provider.** OpenRouter sends what it is going to charge for the request;
   pydantic-ai keeps it in ``ModelResponse.provider_details['cost']``. That is the
   invoice, and it wins over anything a table says.
2. **The bundled table.** pydantic-ai prices every other request with
   `genai-prices <https://github.com/pydantic/genai-prices>`_ and leaves the result
   in ``usage.cost``: those tokens at the published rate. It is an estimate — a
   reseller routes to whatever upstream is cheapest, and a discount or a surcharge
   is invisible to a list price.
3. **The vendor's rate.** A provider the table does not know by name — OpenCode
   serves the vendored models under its own name — leaves ``usage.cost`` empty, and
   the *model* is then priced against the vendor that publishes it. Also an
   estimate, and the conservative one: a reseller is rarely dearer than the list.

None of the three can answer a **subscription**. A ChatGPT plan signs in with
OAuth and the API bills nothing per token, so the total is what those tokens would
have cost at the published rate — the honest comparison, and the same figure
Claude Code shows its subscribers. The panel marks the estimate with `~` for that
reason.

A model no table knows, or a response that arrived without usage, is not counted
at all: the total is then a **floor**, marked `+`, instead of a smaller lie.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from genai_prices import calc_price
from pydantic_ai.messages import ModelMessage, ModelResponse

EXACT_DECIMALS = 4
"""Decimals of `/context`. One request can cost a fraction of a cent."""

SMALLEST_SHOWN = Decimal(1).scaleb(-EXACT_DECIMALS)
"""Below this an amount is a floor, not a zero: `< $0.0001`."""


@dataclass(frozen=True, slots=True)
class SessionCost:
    """The bill of a session, and how much of it is known.

    ``total`` is ``None`` only when there are requests and none of them could be
    priced: a real zero (nothing sent yet) is ``Decimal(0)``.
    """

    total: Decimal | None = None
    reported: int = 0
    """Requests priced by the provider itself: the amount it is going to charge."""
    estimated: int = 0
    """Requests priced from a table: what they cost at the published rate."""
    unpriced: int = 0
    """Requests with usage that no table and no provider could price."""
    unmeasured: int = 0
    """Requests with no usage at all: an old session, or a provider that omits it."""

    @property
    def priced(self) -> int:
        """Requests inside ``total``."""
        return self.reported + self.estimated

    @property
    def requests(self) -> int:
        """Requests the session sent, priced or not."""
        return self.priced + self.unpriced + self.unmeasured

    @property
    def approximate(self) -> bool:
        """Part of the total is a list price rather than a charge."""
        return self.estimated > 0

    @property
    def partial(self) -> bool:
        """Something was left out, so the total is a floor."""
        return self.unpriced > 0 or self.unmeasured > 0

    @property
    def exact(self) -> bool:
        """Every request was priced, and by the provider itself."""
        return self.priced > 0 and not self.approximate and not self.partial


def _amount(value: object) -> Decimal | None:
    """A non-negative finite amount, from the int, float, str or Decimal we get."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def reported_cost(response: ModelResponse) -> Decimal | None:
    """What the provider said it would charge, if it said anything.

    OpenRouter is the case this exists for: its ``usage.cost`` is the number the
    account is charged, and pydantic-ai files it under ``provider_details`` next to
    the upstream it routed to. The Pydantic AI Gateway takes the other route and
    writes the reported cost straight into ``usage.cost`` (see ``request_amount``).
    """
    details = response.provider_details
    if not isinstance(details, dict):
        return None
    return _amount(details.get('cost'))


def request_amount(response: ModelResponse) -> tuple[Decimal | None, bool]:
    """The amount of one response, and whether the provider is the one who said it."""
    reported = reported_cost(response)
    if reported is not None:
        return reported, True
    return _amount(response.usage.cost), False


def _table_price(response: ModelResponse) -> Decimal | None:
    """The model at its vendor's rate, for the providers pydantic-ai cannot name.

    pydantic-ai prices a response against the provider's URL and name, and leaves
    ``usage.cost`` empty when neither is in the table. The model id usually still
    resolves on its own, which is what makes OpenCode's catalogue priceable.
    """
    usage = response.usage
    if not response.model_name or usage.unmeasured_requests or not usage.has_values():
        return None
    key = _price_key(response)
    if key is not None and key in _PRICE_CACHE:
        return _PRICE_CACHE[key]
    try:
        price = calc_price(usage, response.model_name)
    except LookupError, ValueError, TypeError:
        # An unknown model, or usage a table cannot price (cache counts that imply
        # a negative uncached remainder). Both are expected; pricing never raises.
        amount = None
    else:
        amount = _amount(price.total_price)
    if key is not None:
        if len(_PRICE_CACHE) >= _PRICE_CACHE_LIMIT:
            # A price is static for the life of the process —nothing here calls
            # `update_in_background`— so dropping the whole table costs a
            # recomputation and never an answer.
            _PRICE_CACHE.clear()
        _PRICE_CACHE[key] = amount
    return amount


def _price_key(response: ModelResponse) -> tuple | None:
    """The inputs of one price, or ``None`` when they cannot be hashed.

    The panel recomputes the bill at every checkpoint of a turn, so the same
    responses are priced over and over: a lookup table is what keeps a long
    session from re-reading the price data at every step. The key covers every
    field the response carries except the ones pricing ignores, so a usage
    dimension added by a future pydantic-ai is a new key, never a stale hit.
    """
    items: list[tuple[str, object]] = []
    for name, value in response.usage.__dict__.items():
        if name in _NOT_PRICED:
            continue
        try:
            hash(value)
        except TypeError:
            return None
        items.append((name, value))
    return (response.model_name, tuple(sorted(items)))


_PRICE_CACHE: dict[tuple, Decimal | None] = {}
_PRICE_CACHE_LIMIT = 1024

_NOT_PRICED = frozenset({'details', 'cost', 'unmeasured_requests'})
"""Fields of a usage that are not an input of its price.

``details`` is provider bookkeeping and unhashable, ``cost`` is an output, and an
unmeasured request is not priced at all.
"""


def session_cost(messages: Sequence[ModelMessage]) -> SessionCost:
    """Adds up the bill of the session, request by request.

    Only ``ModelResponse`` carries usage: the prompt of a turn is billed with the
    answer it produced, so counting the request too would price it twice.
    """
    total = Decimal(0)
    reported = estimated = unpriced = unmeasured = sent = 0
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        sent += 1
        usage = message.usage
        if not usage.has_values() or usage.unmeasured_requests:
            # No tokens to price: the amount would be a guess about a blind spot.
            unmeasured += 1
            continue
        amount, is_reported = request_amount(message)
        if amount is None:
            amount = _table_price(message)
            if amount is None:
                unpriced += 1
                continue
            is_reported = False
        if is_reported:
            reported += 1
        else:
            estimated += 1
        total += amount

    if not sent:
        return SessionCost(total=Decimal(0))
    if not (reported or estimated):
        return SessionCost(total=None, unpriced=unpriced, unmeasured=unmeasured)
    return SessionCost(
        total=total,
        reported=reported,
        estimated=estimated,
        unpriced=unpriced,
        unmeasured=unmeasured,
    )


def format_usd(amount: Decimal | None) -> str:
    """`$0.0432`, `$0`, `<$0.0001` or `?`.

    Four decimals because the first request of a session is worth a fraction of a
    cent, and rounding it to `$0.00` reads as free.
    """
    if amount is None:
        return '?'
    if amount <= 0:
        return '$0'
    rounded = amount.quantize(SMALLEST_SHOWN, rounding=ROUND_HALF_UP)
    if rounded == 0:
        return f'<${SMALLEST_SHOWN}'
    return f'${rounded}'
