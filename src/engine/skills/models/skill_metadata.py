"""Level 1 of the Agent Skills protocol: cheap skill metadata."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """Level 1 of the protocol: cheap skill metadata.

    It is the only thing always injected into the model context.
    """

    name: str
    description: str
    path: Path
    skill_file: Path
    source: str
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: tuple[str, ...] = ()
    model_invocable: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()
