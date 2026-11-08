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

from src.engine.context.budget import DEFAULT_OUTPUT_RESERVE
from src.engine.context.compaction.constants import (
    REASONING_TARGET_PERCENT,
    REASONING_TARGET_STEPS,
)
from src.engine.context.strategy.constants import (
    DEFAULT_IMAGE_MAX_PERCENT,
    DEFAULT_IMAGE_TURNS,
    DEFAULT_KEEP_RECENT_PROMPTS,
)

ENV_MAX_STEPS = 'SIGIL_MAX_STEPS'
ENV_MODEL_MAX_ATTEMPTS = 'SIGIL_MODEL_MAX_ATTEMPTS'
ENV_CONTEXT_STRATEGY = 'SIGIL_CONTEXT_STRATEGY'
ENV_CONTEXT_REASONING_STEPS = 'SIGIL_CONTEXT_REASONING_STEPS'
ENV_CONTEXT_REASONING_MAX_PERCENT = 'SIGIL_CONTEXT_REASONING_MAX_PERCENT'
ENV_CONTEXT_IMAGE_ROUNDS = 'SIGIL_CONTEXT_IMAGE_ROUNDS'
ENV_CONTEXT_IMAGE_MAX_PERCENT = 'SIGIL_CONTEXT_IMAGE_MAX_PERCENT'
ENV_CONTEXT_KEEP_RECENT_TURNS = 'SIGIL_CONTEXT_KEEP_RECENT_TURNS'
ENV_CONTEXT_OUTPUT_RESERVE = 'SIGIL_CONTEXT_OUTPUT_RESERVE'

DEFAULT_MAX_STEPS = 256
"""Model calls per run before the agent is asked for a last, tool-less answer.

It is not an abort: the steps are taken away and the model gets one more call to
report what it established, so a long turn ends with a summary instead of dying.
A profile can declare its own budget with `max_steps` in its frontmatter.
"""

DEFAULT_MODEL_MAX_ATTEMPTS = 5
"""Total attempts per model request, the first one included."""

CONTEXT_STRATEGIES = ('windowed', 'compacting', 'passthrough')

DEFAULT_CONTEXT_STRATEGY = 'windowed'


@dataclass(frozen=True, slots=True)
class SigilSettings:
    """Settings of a session. Immutable on purpose: a turn never mutates them."""

    max_steps: int = DEFAULT_MAX_STEPS
    model_max_attempts: int = DEFAULT_MODEL_MAX_ATTEMPTS
    context_strategy: str = DEFAULT_CONTEXT_STRATEGY
    reasoning_steps: int = REASONING_TARGET_STEPS
    """Cross-turn count target; operational maintenance starts at its ceiling."""
    reasoning_max_percent: int = REASONING_TARGET_PERCENT
    """Known reasoning size target as a percentage of the model window."""
    image_turns: int = DEFAULT_IMAGE_TURNS
    """Rounds an image stays loaded, counting the one it arrived in."""
    image_max_percent: int = DEFAULT_IMAGE_MAX_PERCENT
    """Ceiling for the pixels that stay, as a percentage of the model window."""
    keep_recent_prompts: int = DEFAULT_KEEP_RECENT_PROMPTS
    output_reserve: int = DEFAULT_OUTPUT_RESERVE

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError('max_steps must be at least 1')
        if self.model_max_attempts < 1:
            raise ValueError('model_max_attempts must be at least 1')
        if self.context_strategy not in CONTEXT_STRATEGIES:
            supported = ', '.join(CONTEXT_STRATEGIES)
            raise ValueError(f'context_strategy must be one of {supported}; got {self.context_strategy!r}')
        if self.reasoning_steps < 1:
            raise ValueError('reasoning_steps must be at least 1')
        if not 0 <= self.reasoning_max_percent <= 100:
            raise ValueError('reasoning_max_percent must be between 0 and 100')
        if self.image_turns < 1:
            raise ValueError('image_turns must be at least 1: the turn in progress always travels')
        if self.image_max_percent < 0:
            raise ValueError('image_max_percent cannot be negative')
        if self.keep_recent_prompts < 1:
            raise ValueError('keep_recent_prompts must be at least 1')
        if self.output_reserve < 0:
            raise ValueError('output_reserve cannot be negative')


def _read_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, '').strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f'{name} must be an integer; got {raw!r}') from exc


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
        reasoning_steps=_read_int(env, ENV_CONTEXT_REASONING_STEPS, REASONING_TARGET_STEPS),
        reasoning_max_percent=_read_int(env, ENV_CONTEXT_REASONING_MAX_PERCENT, REASONING_TARGET_PERCENT),
        image_turns=_read_int(env, ENV_CONTEXT_IMAGE_ROUNDS, DEFAULT_IMAGE_TURNS),
        image_max_percent=_read_int(env, ENV_CONTEXT_IMAGE_MAX_PERCENT, DEFAULT_IMAGE_MAX_PERCENT),
        keep_recent_prompts=_read_int(env, ENV_CONTEXT_KEEP_RECENT_TURNS, DEFAULT_KEEP_RECENT_PROMPTS),
        output_reserve=_read_int(env, ENV_CONTEXT_OUTPUT_RESERVE, DEFAULT_OUTPUT_RESERVE),
    )
