"""The SiblingCall value of the tooling layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SiblingCall:
    """A call emitted before the current one in the same response."""

    tool_name: str
    args: dict[str, Any]
    tool_call_id: str | None
