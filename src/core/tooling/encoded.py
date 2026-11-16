"""The encoded helper of the tooling layer."""

from __future__ import annotations

import json
from typing import Any

from src.core.tooling.normalize import normalize


def encoded(value: Any) -> str:
    """The JSON size used by the generic policy, including escaping and separators."""
    return json.dumps(normalize(value), ensure_ascii=False, default=str)
