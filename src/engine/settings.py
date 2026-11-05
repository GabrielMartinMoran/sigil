"""Runtime settings of the application.

These are the knobs that tune a session (step limits, retries, context
strategy). They are read from the environment **once**, at startup, by the
composition root, and then passed inward as an immutable value object.

The difference with the previous ``ConfigProvider`` matters: that class read
``os.environ`` in its class body, so importing any module that touched it froze
the values and pulled the repository ``.env`` into the process. A frozen
dataclass built by an explicit loader can be constructed in a test with no
environment at all.

Provider credentials are *not* here. They belong to the provider accounts the
user configures from the UI (``src/engine/providers``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

ENV_MAX_STEPS = 'SIGIL_MAX_STEPS'
ENV_MODEL_MAX_ATTEMPTS = 'SIGIL_MODEL_MAX_ATTEMPTS'
ENV_CONTEXT_STRATEGY = 'SIGIL_CONTEXT_STRATEGY'
ENV_CONTEXT_KEEP_THINKING = 'SIGIL_CONTEXT_KEEP_THINKING'
ENV_CONTEXT_KEEP_RECENT_TURNS = 'SIGIL_CONTEXT_KEEP_RECENT_TURNS'
ENV_CONTEXT_MAX_TOOL_RESULT_CHARS = 'SIGIL_CONTEXT_MAX_TOOL_RESULT_CHARS'
ENV_CONTEXT_OUTPUT_RESERVE = 'SIGIL_CONTEXT_OUTPUT_RESERVE'

DEFAULT_MAX_STEPS = 256
"""Hard step limit per turn: every model response and every tool call count."""

DEFAULT_MODEL_MAX_ATTEMPTS = 5
"""Total attempts per model request, the first one included."""

CONTEXT_STRATEGIES = ('windowed', 'compacting', 'passthrough')

DEFAULT_CONTEXT_STRATEGY = 'windowed'
DEFAULT_KEEP_RECENT_TURNS = 3
DEFAULT_MAX_TOOL_RESULT_CHARS = 4000
DEFAULT_OUTPUT_RESERVE = 8000
DEFAULT_KEEP_THINKING_STEPS = 4
"""Reasoning blocks kept from the round in progress, before trimming starts."""

_TRUTHY = frozenset({'1', 'true', 'yes', 'on'})
_THINKING_ALL = frozenset({'all', 'on', 'true', 'yes', '1', '-'})
_THINKING_OFF = frozenset({'off', 'none', 'no', 'false', '0'})


@dataclass(frozen=True, slots=True)
class SigilSettings:
    """Settings of a session. Immutable on purpose: a turn never mutates them."""

    max_steps: int = DEFAULT_MAX_STEPS
    model_max_attempts: int = DEFAULT_MODEL_MAX_ATTEMPTS
    context_strategy: str = DEFAULT_CONTEXT_STRATEGY
    keep_thinking: int | None = DEFAULT_KEEP_THINKING_STEPS
    """Reasoning blocks kept from the round in progress.

    ``0`` drops all reasoning, ``None`` keeps every block. The window is on steps,
    not on the round: resending a whole round is quadratic.
    """
    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS
    max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS
    output_reserve: int = DEFAULT_OUTPUT_RESERVE

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError('max_steps must be at least 1')
        if self.model_max_attempts < 1:
            raise ValueError('model_max_attempts must be at least 1')
        if self.context_strategy not in CONTEXT_STRATEGIES:
            supported = ', '.join(CONTEXT_STRATEGIES)
            raise ValueError(f'context_strategy must be one of {supported}; got {self.context_strategy!r}')
        if self.keep_recent_turns < 1:
            raise ValueError('keep_recent_turns must be at least 1')
        if self.max_tool_result_chars < 200:
            raise ValueError('max_tool_result_chars must be at least 200')
        if self.output_reserve < 0:
            raise ValueError('output_reserve cannot be negative')
        if self.keep_thinking is not None and self.keep_thinking < 0:
            raise ValueError('keep_thinking cannot be negative')


def _read_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f'{name} must be an integer; got {raw!r}') from exc


def _read_bool(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, '').strip().lower() in _TRUTHY


def _read_keep_thinking(env: Mapping[str, str]) -> int | None:
    """Reasoning policy, as the number of steps to keep from the current round.

    ``off``/``none``/``0`` drops all reasoning. ``all``, or any of the legacy truthy
    values (``1``, ``true``, ``yes``, ``on``), keeps every block, so an existing
    `SIGIL_CONTEXT_KEEP_THINKING=1` behaves exactly as it did. Any other number keeps
    that many steps of the round in progress.

    ``1`` is the one value that cannot be read as a count: it was the documented way
    to turn this on, and it stays "keep everything". Use ``2`` or more for steps.
    """
    raw = env.get(ENV_CONTEXT_KEEP_THINKING, '').strip().lower()
    if not raw:
        return DEFAULT_KEEP_THINKING_STEPS
    if raw in _THINKING_ALL:
        return None
    if raw in _THINKING_OFF:
        return 0
    try:
        steps = int(raw)
    except ValueError as exc:
        raise ValueError(f'{ENV_CONTEXT_KEEP_THINKING} must be a number of steps, "all" or "off"; got {raw!r}') from exc
    if steps < 0:
        raise ValueError(f'{ENV_CONTEXT_KEEP_THINKING} cannot be negative; got {steps}')
    return steps


def load_settings(environ: Mapping[str, str] | None = None) -> SigilSettings:
    """Builds the settings from the environment.

    Args:
        environ: Environment to read from (``os.environ`` by default).

    Raises:
        ValueError: When a value is present but cannot be parsed. A typo in a
            limit should fail loudly at startup, not silently fall back.
    """
    env = os.environ if environ is None else environ
    return SigilSettings(
        max_steps=_read_int(env, ENV_MAX_STEPS, DEFAULT_MAX_STEPS),
        model_max_attempts=_read_int(env, ENV_MODEL_MAX_ATTEMPTS, DEFAULT_MODEL_MAX_ATTEMPTS),
        context_strategy=env.get(ENV_CONTEXT_STRATEGY, '').strip().lower() or DEFAULT_CONTEXT_STRATEGY,
        keep_thinking=_read_keep_thinking(env),
        keep_recent_turns=_read_int(env, ENV_CONTEXT_KEEP_RECENT_TURNS, DEFAULT_KEEP_RECENT_TURNS),
        max_tool_result_chars=_read_int(env, ENV_CONTEXT_MAX_TOOL_RESULT_CHARS, DEFAULT_MAX_TOOL_RESULT_CHARS),
        output_reserve=_read_int(env, ENV_CONTEXT_OUTPUT_RESERVE, DEFAULT_OUTPUT_RESERVE),
    )
