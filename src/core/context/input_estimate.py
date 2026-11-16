"""The estimated input of one request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class InputEstimate:
    total: int
    source: Literal['local', 'provider_plus_delta'] = 'local'
    opaque_reserve: int = 0
