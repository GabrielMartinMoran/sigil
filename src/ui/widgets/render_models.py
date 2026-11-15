"""Helpers that used to live in model_picker_screen."""

from __future__ import annotations

from collections.abc import Sequence

from src.core.providers.domain.model_capability import ModelCatalog


def render_models(catalog: ModelCatalog) -> Sequence[str]:
    """Plain rows, for `/model --list` and for tests."""
    return [f'{model.name} ({model.window_label}, {model.efforts_label})' for model in catalog.models]
