"""The model_settings helper of the core layer."""

from __future__ import annotations

from typing import Any

from src.core.providers.domain.thinking import settings_value as thinking_settings


def model_settings(model_service: Any) -> dict[str, Any] | None:
    """The reasoning level in effect, in the shape a run takes.

    Subagents run on the same level as the parent: it is the same account and the
    same model, and a subagent quietly reasoning less than the agent that summoned
    it would be a difference nobody asked for.
    """
    value = thinking_settings(model_service.snapshot().thinking)
    return None if value is None else {'thinking': value}
