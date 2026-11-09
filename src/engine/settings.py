"""Runtime settings of the application.

These are the knobs that tune a session (step limits, retries, context strategy). They
are read from the environment **once**, at startup, by the composition root, and then
passed inward as an immutable value object.

The difference with the previous ``ConfigProvider`` matters: that class read
``os.environ`` in its class body, so importing any module that touched it froze the
values and pulled the repository ``.env`` into the process. A frozen dataclass built by
an explicit loader can be constructed in a test with no environment at all.

Provider credentials are *not* here. They belong to the provider accounts the user
configures from the UI (``src/engine/providers``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from src.engine.context.budget import DEFAULT_OUTPUT_RESERVE, DEFAULT_SAFETY_MARGIN_PERCENT
from src.engine.context.compaction.policy import DEFAULT_POLICY
from src.engine.context.strategy.constants import (
    DEFAULT_IMAGE_MAX_PERCENT,
    DEFAULT_IMAGE_TURNS,
)

ENV_MAX_STEPS = 'SIGIL_MAX_STEPS'
ENV_MODEL_MAX_ATTEMPTS = 'SIGIL_MODEL_MAX_ATTEMPTS'
ENV_CONTEXT_STRATEGY = 'SIGIL_CONTEXT_STRATEGY'
ENV_CONTEXT_TRIGGER_PERCENT = 'SIGIL_CONTEXT_TRIGGER_PERCENT'
ENV_CONTEXT_TARGET_PERCENT = 'SIGIL_CONTEXT_TARGET_PERCENT'
ENV_CONTEXT_RECENT_REASONING_BLOCKS = 'SIGIL_CONTEXT_RECENT_REASONING_BLOCKS'
ENV_CONTEXT_RECENT_TOOL_CALLS = 'SIGIL_CONTEXT_RECENT_TOOL_CALLS'
ENV_CONTEXT_SAFETY_MARGIN_PERCENT = 'SIGIL_CONTEXT_SAFETY_MARGIN_PERCENT'
ENV_CONTEXT_IMAGE_ROUNDS = 'SIGIL_CONTEXT_IMAGE_ROUNDS'
ENV_CONTEXT_IMAGE_MAX_PERCENT = 'SIGIL_CONTEXT_IMAGE_MAX_PERCENT'
ENV_CONTEXT_OUTPUT_RESERVE = 'SIGIL_CONTEXT_OUTPUT_RESERVE'

DEFAULT_MAX_STEPS = 256
"""Model calls per run before the agent is asked for a last, tool-less answer.

It is not an abort: the steps are taken away and the model gets one more call to report
what it established, so a long turn ends with a summary instead of dying. A profile can
declare its own budget with `max_steps` in its frontmatter.
"""

DEFAULT_MODEL_MAX_ATTEMPTS = 5
"""Total attempts per model request, the first one included."""

CONTEXT_STRATEGIES = ('append-only', 'passthrough')
"""`append-only` compacts under pressure; `passthrough` sends the log as it is."""

DEFAULT_CONTEXT_STRATEGY = 'append-only'


@dataclass(frozen=True, slots=True)
class SigilSettings:
    """Settings of a session. Immutable on purpose: a turn never mutates them."""

    max_steps: int = DEFAULT_MAX_STEPS
    model_max_attempts: int = DEFAULT_MODEL_MAX_ATTEMPTS
    context_strategy: str = DEFAULT_CONTEXT_STRATEGY
    context_trigger_percent: int = DEFAULT_POLICY.trigger_percent
    """Share of the window above which deterministic compaction runs."""
    context_target_percent: int = DEFAULT_POLICY.target_percent
    """Share of the window deterministic compaction should leave behind."""
    context_recent_reasoning_blocks: int = DEFAULT_POLICY.recent_reasoning_blocks
    context_recent_tool_calls: int = DEFAULT_POLICY.recent_tool_calls
    context_safety_margin_percent: int = DEFAULT_SAFETY_MARGIN_PERCENT
    image_turns: int = DEFAULT_IMAGE_TURNS
    """Rounds an image stays loaded, counting the one it arrived in."""
    image_max_percent: int = DEFAULT_IMAGE_MAX_PERCENT
    """Ceiling for the pixels that stay, as a percentage of the model window."""
    output_reserve: int = DEFAULT_OUTPUT_RESERVE

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError('max_steps must be at least 1')
        if self.model_max_attempts < 1:
            raise ValueError('model_max_attempts must be at least 1')
        if self.context_strategy not in CONTEXT_STRATEGIES:
            supported = ', '.join(CONTEXT_STRATEGIES)
            raise ValueError(f'context_strategy must be one of {supported}; got {self.context_strategy!r}')
        if not 0 < self.context_target_percent < self.context_trigger_percent <= 100:
            raise ValueError('context percentages must satisfy 0 < target < trigger <= 100')
        if self.context_recent_reasoning_blocks < 0 or self.context_recent_tool_calls < 0:
            raise ValueError('context retention counts must be non-negative')
        if not 0 <= self.context_safety_margin_percent < 100:
            raise ValueError('context_safety_margin_percent must be between 0 and 99')
        if self.image_turns < 1:
            raise ValueError('image_turns must be at least 1: the turn in progress always travels')
        if self.image_max_percent < 0:
            raise ValueError('image_max_percent cannot be negative')
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
        ValueError: When a value is present but cannot be parsed. A typo in a limit
            should fail loudly at startup, not silently fall back.
    """
    env = os.environ if environ is None else environ
    return SigilSettings(
        max_steps=_read_int(env, ENV_MAX_STEPS, DEFAULT_MAX_STEPS),
        model_max_attempts=_read_int(env, ENV_MODEL_MAX_ATTEMPTS, DEFAULT_MODEL_MAX_ATTEMPTS),
        context_strategy=env.get(ENV_CONTEXT_STRATEGY, '').strip().lower() or DEFAULT_CONTEXT_STRATEGY,
        context_trigger_percent=_read_int(env, ENV_CONTEXT_TRIGGER_PERCENT, DEFAULT_POLICY.trigger_percent),
        context_target_percent=_read_int(env, ENV_CONTEXT_TARGET_PERCENT, DEFAULT_POLICY.target_percent),
        context_recent_reasoning_blocks=_read_int(
            env, ENV_CONTEXT_RECENT_REASONING_BLOCKS, DEFAULT_POLICY.recent_reasoning_blocks
        ),
        context_recent_tool_calls=_read_int(env, ENV_CONTEXT_RECENT_TOOL_CALLS, DEFAULT_POLICY.recent_tool_calls),
        context_safety_margin_percent=_read_int(env, ENV_CONTEXT_SAFETY_MARGIN_PERCENT, DEFAULT_SAFETY_MARGIN_PERCENT),
        image_turns=_read_int(env, ENV_CONTEXT_IMAGE_ROUNDS, DEFAULT_IMAGE_TURNS),
        image_max_percent=_read_int(env, ENV_CONTEXT_IMAGE_MAX_PERCENT, DEFAULT_IMAGE_MAX_PERCENT),
        output_reserve=_read_int(env, ENV_CONTEXT_OUTPUT_RESERVE, DEFAULT_OUTPUT_RESERVE),
    )
