"""The ToolCallEvent value of the tooling layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.core.tooling.tool_summary import ToolSummary

ToolPhase = Literal['start', 'success', 'error']


@dataclass(frozen=True, slots=True)
class ToolCallEvent:
    """Event emitted by a tool's wrapper."""

    tool_name: str
    phase: ToolPhase
    args: dict[str, Any] = field(default_factory=dict)
    duration_ms: float | None = None
    result_preview: str | None = None
    error: str | None = None
    summary: ToolSummary | None = None
    """What the tool says about its result, when it declares a presenter.

    It is not the same as ``result_preview``: the preview is the raw result cut to one
    line, for anyone inspecting the log, while the summary is the sentence the tool
    chose because it is the one worth reading. The chat shows this one.
    """
    invocation_id: str = ''
    """Stable logging identity across start and finish, independent of the tool name."""
    node_id: str | None = None
    """Subagent execution, or None for the session root."""
    output: str | None = None
    """Human-formatted output, separate from the raw logging preview."""
