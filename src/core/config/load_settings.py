"""The load_settings helper of the core layer."""

from __future__ import annotations

import os
from collections.abc import Mapping

from src.core.config.sigil_settings import (
    DEFAULT_CONTEXT_STRATEGY,
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL_MAX_ATTEMPTS,
    SigilSettings,
)
from src.core.context.compaction.compaction_policy import DEFAULT_POLICY
from src.core.context.context_budget import DEFAULT_OUTPUT_RESERVE, DEFAULT_SAFETY_MARGIN_PERCENT
from src.core.context.strategy.constants import (
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
