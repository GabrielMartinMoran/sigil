"""Compact dollar amounts for the side panel.

Same discipline as `test_token_format`: `$0.0432` does not fit next to the window
it belongs to, and the fourth decimal only matters while the amount is smaller
than a cent.
"""

from __future__ import annotations

from decimal import Decimal

from src.ui.money_format import UNKNOWN, compact_usd


def test_a_cent_or_more_is_shown_with_two_decimals():
    assert compact_usd(Decimal('0.01')) == '$0.01'
    assert compact_usd(Decimal('0.0432')) == '$0.04'
    assert compact_usd(Decimal('1.2')) == '$1.20'
    assert compact_usd(Decimal('123.456')) == '$123.46'


def test_below_a_cent_keeps_four_decimals():
    """The first requests of a session are worth a fraction of a cent."""
    assert compact_usd(Decimal('0.0043')) == '$0.0043'
    assert compact_usd(Decimal('0.0021')) == '$0.0021'


def test_below_a_cent_a_trailing_zero_is_dropped():
    """Nothing is padded: under a cent, every digit shown moved the number."""
    assert compact_usd(Decimal('0.004')) == '$0.004'
    assert compact_usd(Decimal('0.00496')) == '$0.005'


def test_an_amount_that_rounds_to_a_cent_reads_like_one():
    assert compact_usd(Decimal('0.005')) == '$0.01'
    assert compact_usd(Decimal('0.00996')) == '$0.01'


def test_something_too_small_to_show_is_a_floor():
    assert compact_usd(Decimal('0.00001')) == '<$0.0001'


def test_zero_is_zero():
    assert compact_usd(Decimal(0)) == '$0'


def test_missing_amount_is_a_question_mark():
    assert compact_usd(None) == UNKNOWN
