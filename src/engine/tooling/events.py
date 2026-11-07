"""Events emitted by a tool's wrapper.

An event describes a moment in the life of a call (it started, it finished fine,
it failed) with the minimum a sink needs to show it: name, args,
duration and a summary of the result or the error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.engine.tooling.presentation import ToolSummary

ToolPhase = Literal['start', 'success', 'error']
"""Phase of a call: started, finished fine or failed."""

PREVIEW_LIMIT = 200
"""Characters kept in a one-line summary."""


def preview(value: Any, limit: int = PREVIEW_LIMIT) -> str:
    """One-line summary, safe for any value.

    Logging must never break a tool, so a ``__str__`` that blows up
    falls back to the object's ``repr`` instead of propagating the exception.
    """
    try:
        text = ' '.join(str(value).split())
    except Exception:  # noqa: BLE001 - logging must never break a tool
        text = object.__repr__(value)
    return text if len(text) <= limit else text[: limit - 1] + '…'


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
    output: str | None = None
    """Human-formatted output, separate from the raw logging preview."""
