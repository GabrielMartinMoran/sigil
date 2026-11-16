"""Validated JSON contract for content-free agent diagnostics.

ViewResult, model messages, compaction summaries, instruction bodies and tool
schemas never cross this boundary. Renderers remain client-side.
"""

from dataclasses import dataclass

from pydantic import TypeAdapter

from src.core.context.meter.debug_report import DebugReport


@dataclass(frozen=True, slots=True)
class DebugSnapshot:
    session_id: str
    report: DebugReport
    timing: str | None = None
    node_id: str | None = None


DEBUG_REPORT_ADAPTER = TypeAdapter(DebugReport)
DEBUG_SNAPSHOT_ADAPTER = TypeAdapter(DebugSnapshot)
