"""Compact token counts for the side panel.

A panel that is 34 columns wide cannot afford `201,326 / 1,000,000`: the pair
steals the whole line. These counts are shown for orientation, not for
accounting —`/context` and the debug view keep the exact numbers.
"""

from __future__ import annotations

import math

UNKNOWN = '?'
"""What a missing number looks like. A blank gap reads as zero."""

_THOUSAND = 1_000
_MILLION = 1_000_000

_DECIMALS_BELOW_THOUSANDS = 100
"""Ceiling for the single decimal in `K`.

The decimal is noise once the number is long enough to stand on its own:
`132.4K` spends four characters saying something nobody acts on. Below 100K the
number is still short, and `12.4K` against `12K` is a visible 3% of the window.
"""


def _round_half_up(value: float) -> int:
    """Rounds away from the banker's rounding `round` does on exact halves.

    `2.5M` should read `3M`, not `2M`: nobody looking at a million tokens cares
    about the tie-breaking rule, but the number should not look wrong.
    """
    return math.floor(value + 0.5)


def compact_tokens(value: int | None) -> str:
    """Token count with a `K` or `M` suffix, sized to stay short.

    ``950``, ``5.5K``, ``132K``, ``1M``. Millions never carry a decimal: at that
    scale `.4M` is 400,000 tokens and the reader is not counting them.
    """
    if value is None:
        return UNKNOWN
    if value < _THOUSAND:
        return str(value)
    if value >= _MILLION:
        return f'{_round_half_up(value / _MILLION)}M'

    thousands = _round_half_up(value / _THOUSAND)
    if thousands >= _THOUSAND:
        # 999,600 rounds up to 1000K, a peer of 1M rather than of 999K.
        return '1M'
    if thousands < _DECIMALS_BELOW_THOUSANDS:
        tenth = _round_half_up(value / 100) / 10
        if tenth != int(tenth):
            return f'{tenth:.1f}K'
    return f'{thousands}K'
