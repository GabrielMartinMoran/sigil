"""Helpers that used to live in model_view_mixin."""

from __future__ import annotations

from src.core.providers.domain.thinking import EFFORT_LEVELS, label
from src.core.providers.model_snapshot import ModelSnapshot


def effort_label(model: ModelSnapshot, in_effect: str) -> str:
    """Readable effort, falling back to the selection when the history says nothing."""
    if in_effect and in_effect in EFFORT_LEVELS:
        return in_effect
    return str(model.thinking) if model.selection is not None else label('default')
