"""View strategy constants."""

from __future__ import annotations

from typing import Literal

DEFAULT_KEEP_RECENT_TURNS = 3
"""Turns kept intact before starting to trim."""

DEFAULT_MAX_TOOL_RESULT_CHARS = 4_000
"""Cap of an old tool result. Recent ones are not touched."""

DEFAULT_TOOL_RESULT_HEAD_CHARS = 400
"""How much of a truncated tool result is kept, so the signal is not lost."""

ReasoningPolicy = Literal['off', 'recent', 'all']
"""What travels back to the model of the reasoning it produced.

- ``off``: none of it.
- ``recent``: the last `DEFAULT_REASONING_STEPS` steps of the round in progress.
- ``all``: every block of the session. Expensive: it is quadratic inside a round.
"""

REASONING_POLICIES: tuple[ReasoningPolicy, ...] = ('off', 'recent', 'all')

DEFAULT_REASONING: ReasoningPolicy = 'recent'
"""The round in progress is where the agent re-reads its own reasoning."""

DEFAULT_REASONING_STEPS = 10
"""Steps of the round in progress whose reasoning travels when the policy is ``recent``.

Resending the whole round is quadratic: a measured 317-step round would bill 44M
extra tokens. The loop this prevents —re-deciding a decision already taken— shows up
over a few consecutive steps, so ten covers it with room to spare, and the token cap
is what keeps the window from being stupid when the context is nearly full.
"""

DEFAULT_REASONING_MAX_PERCENT = 5
"""Ceiling for the reasoning kept, as a percentage of the model's window.

Reasoning is the cheapest thing to drop and the most voluminous to carry, so on a
1M window this is 50k tokens: generous while the conversation is short, and never
a reason to lose a turn when it is not.
"""

DEFAULT_IMAGE_ROUNDS = 3
"""Rounds an image stays loaded, counting the one it arrived in.

The round is `run_id`, the same unit the reasoning window uses, so an image loaded
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


def window_for(policy: ReasoningPolicy, steps: int) -> int | None:
    """How many steps keep their reasoning, as the strategies understand it.

    ``0`` is "none" and ``None`` is "no limit"; the policy is what says which, so no
    caller has to pass a magic number to mean a mode.
    """
    if policy == 'off':
        return 0
    if policy == 'all':
        return None
    return steps


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
"""Tools whose result is **instruction**, not data: they are never truncated or compacted.

A skill body is what the agent has to keep doing. Truncating it to
a stub or collapsing it into "load_skill -> ok" takes the instructions away mid-
task. Measured: the installed skills weigh between 454 and 8,256 tokens.
"""
