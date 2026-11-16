"""The ToolDeclaration value of the tooling layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.tooling.tool_compactor import ToolCompactor
from src.core.tooling.tool_permission import ToolPermission


@dataclass(frozen=True, slots=True)
class ToolDeclaration:
    """One tool: what it needs, what it is allowed to do, and what it keeps."""

    name: str
    permission: ToolPermission | None = None
    requires: frozenset[str] = frozenset()
    """Model features the tool needs. Empty means every route can use it."""

    compaction: ToolCompactor | None = None
    """How the tool's arguments and results are reduced when history is compacted.

    ``None`` means the tool did not declare one, which is allowed only for tools this
    project does not own (MCP servers, external tools). `uncovered()` is what turns
    that into a failure for our own.
    """
