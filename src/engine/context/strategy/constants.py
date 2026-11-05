"""View strategy constants."""

from __future__ import annotations

DEFAULT_KEEP_RECENT_TURNS = 3
"""Turns kept intact before starting to trim."""

DEFAULT_MAX_TOOL_RESULT_CHARS = 4_000
"""Cap of an old tool result. Recent ones are not touched."""

DEFAULT_TOOL_RESULT_HEAD_CHARS = 400
"""How much of a truncated tool result is kept, so the signal is not lost."""

DEFAULT_KEEP_THINKING_STEPS = 4
"""Reasoning blocks kept from the round in progress.

Resending the whole round is quadratic: a measured 317-step round would bill 44M
extra tokens. The loop this prevents —re-deciding a decision already taken— shows
up over a few consecutive steps, not over a whole round.
"""

PINNED_TOOLS: frozenset[str] = frozenset({'load_skill', 'read_skill_resource'})
"""Tools whose result is **instruction**, not data: they are never truncated or compacted.

A skill body is what the agent has to keep doing. Truncating it to
a stub or collapsing it into "load_skill -> ok" takes the instructions away mid-
task. Measured: the installed skills weigh between 454 and 8,256 tokens.
"""
