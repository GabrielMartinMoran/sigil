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

DEFAULT_REASONING_STEPS = 4
"""Steps of the round in progress whose reasoning travels when the policy is ``recent``.

Resending the whole round is quadratic: a measured 317-step round would bill 44M
extra tokens, against 1,905 for four steps. The loop this prevents —re-deciding a
decision already taken— shows up over a few consecutive steps.
"""


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


PINNED_TOOLS: frozenset[str] = frozenset({'load_skill', 'read_skill_resource'})
"""Tools whose result is **instruction**, not data: they are never truncated or compacted.

A skill body is what the agent has to keep doing. Truncating it to
a stub or collapsing it into "load_skill -> ok" takes the instructions away mid-
task. Measured: the installed skills weigh between 454 and 8,256 tokens.
"""
