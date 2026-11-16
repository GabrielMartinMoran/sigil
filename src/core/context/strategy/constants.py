"""View strategy constants."""

from __future__ import annotations

DEFAULT_IMAGE_TURNS = 3
"""Rounds an image stays loaded, counting the one it arrived in.

The turn is the identifier the library stamps on one run, so an image loaded by a prompt
or by `view_image` is stamped by the message that carries it. Past the window an image
leaves its id behind and `view_image` brings it back.
"""

DEFAULT_IMAGE_MAX_PERCENT = 10
"""Ceiling for the pixels that stay, as a percentage of the model's window.

Separate from the text budgets on purpose: a picture costs what its pixels cost, which is
an order of magnitude more per item than a block of text, and the two should not compete
for the same number.
"""


def image_token_cap(window: int | None, percent: int) -> int | None:
    """A share of the whole window, independent of the remaining history budget."""
    if percent <= 0:
        return 0
    return None if window is None else max(0, window * percent // 100)
