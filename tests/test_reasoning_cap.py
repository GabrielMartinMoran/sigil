"""Tests for the ceiling on the reasoning window.

The step count says how much reasoning is worth keeping. The cap says how much the
context can afford, so a wide window does not become a tax on every request and does
not starve the turn it is meant to help.
"""

from __future__ import annotations

import pytest

from src.engine.context.strategy.constants import reasoning_token_cap


def test_the_share_of_the_window_is_the_cap():
    """10% of 1M is 100k, and that is what a roomy context allows."""
    assert reasoning_token_cap(window=1_000_000, available=900_000, percent=10) == 100_000


def test_the_room_left_wins_when_it_is_smaller():
    """A nearly full context keeps some reasoning, not what the share would allow."""
    assert reasoning_token_cap(window=1_000_000, available=8_000, percent=10) == 8_000


def test_the_share_wins_when_the_room_is_larger():
    """A huge window does not let reasoning take the whole history."""
    assert reasoning_token_cap(window=200_000, available=180_000, percent=5) == 10_000


@pytest.mark.parametrize('percent', [0, -1])
def test_a_zero_share_allows_nothing(percent):
    assert reasoning_token_cap(window=1_000_000, available=900_000, percent=percent) == 0


def test_no_window_and_no_room_means_no_cap():
    """Without a window there is no budget to spend against: the step count decides."""
    assert reasoning_token_cap(window=None, available=None, percent=10) is None


def test_no_window_but_known_room_uses_the_room():
    assert reasoning_token_cap(window=None, available=50_000, percent=10) == 50_000


def test_a_window_without_room_uses_the_share():
    assert reasoning_token_cap(window=1_000_000, available=None, percent=10) == 100_000


def test_negative_room_is_floored_at_zero():
    """A budget that cannot fit even the history leaves nothing for reasoning."""
    assert reasoning_token_cap(window=1_000_000, available=-500, percent=10) == 0


def test_a_fractional_percentage_does_not_round_up():
    """Floor division: a cap that rounds up is a cap that is exceeded."""
    assert reasoning_token_cap(window=1_000, available=1_000, percent=33) == 330
