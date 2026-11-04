"""Compact token counts for the side panel."""

from __future__ import annotations

from src.ui.token_format import UNKNOWN, compact_tokens


def test_below_a_thousand_is_shown_as_is():
    assert compact_tokens(0) == '0'
    assert compact_tokens(950) == '950'
    assert compact_tokens(999) == '999'


def test_missing_value_is_a_question_mark():
    assert compact_tokens(None) == UNKNOWN


def test_thousands_round_to_whole_k():
    assert compact_tokens(1_000) == '1K'
    assert compact_tokens(132_000) == '132K'
    assert compact_tokens(999_400) == '999K'


def test_short_k_values_keep_one_decimal():
    assert compact_tokens(1_500) == '1.5K'
    assert compact_tokens(5_500) == '5.5K'
    assert compact_tokens(12_400) == '12.4K'


def test_a_decimal_that_rounds_to_zero_is_dropped():
    assert compact_tokens(5_000) == '5K'
    assert compact_tokens(12_030) == '12K'


def test_three_digit_k_values_have_no_decimal():
    assert compact_tokens(132_400) == '132K'
    assert compact_tokens(99_500) == '100K'


def test_millions_never_carry_a_decimal():
    assert compact_tokens(1_000_000) == '1M'
    assert compact_tokens(1_234_567) == '1M'
    assert compact_tokens(2_500_000) == '3M'
    assert compact_tokens(15_000_000) == '15M'


def test_a_thousand_k_never_appears():
    # 999,600 rounds up to 1000K, which is a peer of 1M rather than of 999K.
    assert compact_tokens(999_600) == '1M'
