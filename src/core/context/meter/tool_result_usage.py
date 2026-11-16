"""The ToolResultUsage value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ToolResultUsage:
    """Cost of a tool result, to find the ones that eat the window."""

    tool_name: str
    tokens: int
    chars: int
