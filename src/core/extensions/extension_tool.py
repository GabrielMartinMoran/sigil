"""The ExtensionTool value of the core layer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sigil_tools import ToolDeclaration


@dataclass(frozen=True, slots=True)
class ExtensionTool:
    name: str
    declaration: ToolDeclaration
    directory: Path
    project: bool
