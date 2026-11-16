"""The normalize helper of the tooling layer."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize(value: Any) -> Any:
    """Live models and their stored JSON form use exactly the same policy path."""
    if callable(getattr(value, 'model_dump', None)):
        return normalize(value.model_dump())
    if isinstance(value, Mapping):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(item) for item in value]
    return value
