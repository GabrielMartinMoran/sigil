"""One defaults row the screen offers."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.agents.config_scope import ConfigScope
from src.ui.widgets.defaults_row import DefaultsRow


@dataclass(frozen=True, slots=True)
class DefaultsChoice:
    """What the screen returns: which target, in which file, and what to do."""

    row: DefaultsRow
    scope: ConfigScope
    clear: bool = False
