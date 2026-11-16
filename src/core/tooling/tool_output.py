"""The ToolOutput value of the tooling layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.tooling.tool_summary import ToolSummary


@dataclass(frozen=True, slots=True)
class ToolOutput:
    text: str
    summary: ToolSummary | None = None
