"""The model defaults the service projects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from src.core.agents.model_defaults import ConfigScope


@dataclass(frozen=True, slots=True)
class ProjectedDefaults:
    """Where an attached write can go, read from the service's projection.

    The four facts the screen asks about a destination, and nothing about the entries:
    which files exist, where each one is, whether `SIGIL_CONFIG` replaced the pair, and
    what could not be parsed. `effective`, `invalid` and `sources` are deliberately
    absent — those are precedence, and every row arrives carrying the answer the engine
    already gave, so this process never holds a second copy of that rule.
    """

    destinations: tuple[ConfigScope, ...]
    paths: Mapping[ConfigScope, Path]
    fixed: Path | None = None
    errors: tuple[str, ...] = ()

    def path_for(self, scope: ConfigScope) -> Path:
        """The file ``scope`` names."""
        return self.paths[scope]
