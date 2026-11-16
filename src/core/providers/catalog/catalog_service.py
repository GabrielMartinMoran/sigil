"""The catalogue as the rest of the application sees it.

One entry point: give it an account, get a ``ModelCatalog`` back, never an
exception. The remote list is tried first, the static list is the floor, and any
failure is reported in ``ModelCatalog.error`` so the UI can say why the menu is
short instead of pretending everything is fine.
"""

from __future__ import annotations

from src.core.providers.domain.model_capability import ModelCapability


def best_effort_window(models: tuple[ModelCapability, ...], model: str) -> int | None:
    """Window of ``model`` in ``models``, or ``None``."""
    for candidate in models:
        if candidate.name == model:
            return candidate.context_window
    return None
