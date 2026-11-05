"""Compact dollar amounts for the side panel.

A panel that is 34 columns wide cannot afford `$123.4567`, and the fourth decimal
is noise as soon as the amount is a cent: `$0.04` says everything `$0.0432` says
in half the columns. These amounts are shown for orientation, not for accounting
—`/context` keeps the exact figure.

The tiers are the ones the money actually walks through: a session starts below a
cent (where a rounded `$0.00` would read as free), spends a while between a cent
and a dollar, and ends up in whole cents.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

UNKNOWN = '?'
"""What a missing amount looks like. It matches the `?` of `token_format`."""

_CENT = Decimal('0.01')
_FOUR_DECIMALS = Decimal('0.0001')


def compact_usd(amount: Decimal | None) -> str:
    """`$0`, `<$0.0001`, `$0.0043`, `$0.04`, `$1.20`, `$123.46`."""
    if amount is None:
        return UNKNOWN
    cents = amount.quantize(_CENT, rounding=ROUND_HALF_UP)
    if cents > 0:
        # `$0.00996` rounds to a cent and reads like one: keeping four decimals
        # there would show a trailing zero that means nothing.
        return f'${cents}'
    if amount <= 0:
        return '$0'
    small = amount.quantize(_FOUR_DECIMALS, rounding=ROUND_HALF_UP)
    # `normalize` drops the trailing zeros of `0.0050`: under a cent, every digit
    # shown is a digit that moved the number.
    return f'${small.normalize()}' if small > 0 else f'<${_FOUR_DECIMALS}'
