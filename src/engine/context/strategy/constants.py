"""View strategy constants."""

from __future__ import annotations

DEFAULT_KEEP_RECENT_PROMPTS = 3
"""Turns kept intact before starting to trim."""

DEFAULT_IMAGE_TURNS = 3
"""Rounds an image stays loaded, counting the one it arrived in.

The turn is the identifier the library stamps on one run — the same unit the
reasoning window uses — so an image loaded
by a prompt or by `view_image` is stamped by the message that carries it. Past the
window an image leaves its id behind and `view_image` brings it back.
"""

DEFAULT_IMAGE_MAX_PERCENT = 10
"""Ceiling for the pixels that stay, as a percentage of the model's window.

Separate from the reasoning share on purpose: a picture costs what its pixels cost,
which is an order of magnitude more per item than a block of reasoning, and the two
budgets should not compete for the same number.
"""


def image_token_cap(window: int | None, available: int | None, percent: int) -> int | None:
    """Ceiling for the images kept, in tokens. Same shape as the reasoning cap.

    Args:
        window: The model's context window, or ``None`` when it is unknown.
        available: Tokens left for the history, or ``None`` when the window is unknown.
        percent: Share of `window` that pixels may take.

    Returns:
        The cap in tokens, or ``None`` when it cannot be computed.
    """
    return reasoning_token_cap(window, available, percent)


def reasoning_token_cap(window: int | None, available: int | None, percent: int) -> int | None:
    """Ceiling for the reasoning kept, in tokens, from the room the context has left.

    Two bounds, whichever is smaller:

    - a share of the model's whole window, so a wide step count cannot become a
      standing tax on every request;
    - the room left for the history, so a nearly full context keeps some reasoning
      instead of spending what the turn itself needs.

    Args:
        window: The model's context window, or ``None`` when it is unknown.
        available: Tokens left for the history, or ``None`` when the window is
            unknown.
        percent: Share of `window` that reasoning may take. ``0`` means none.

    Returns:
        The cap in tokens, or ``None`` when it cannot be computed (no window and no
        known room), which leaves the step count as the only limit.
    """
    if percent <= 0:
        return 0
    if window is None and available is None:
        return None
    by_window = None if window is None else max(0, window * percent // 100)
    if available is None:
        return by_window
    if by_window is None:
        return max(0, available)
    return min(by_window, max(0, available))


PINNED_TOOLS: frozenset[str] = frozenset({'load_skill', 'read_skill_resource'})
"""Successful skill calls retain their reconstruction trail during turn summarization.

Their original arguments are the resource registry; results still obey semantic LOD.
Budget emergencies may discard these turns last, without changing the original log.
"""
