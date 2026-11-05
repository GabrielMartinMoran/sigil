"""One agent profile: the frontmatter and where it came from.

The body is not here on purpose. Metadata is what the picker and the status bar
need on every repaint; the Markdown body is read once, when the profile becomes
active, and injected as an instruction block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.engine.agents.profile.permissions import ToolPermissions


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Metadata of an agent profile plus the permissions it declares."""

    name: str
    """Identifier used by `/agent` and stamped in the status bar."""

    description: str
    """One line the picker shows so the choice is informed."""

    path: Path
    """Directory that holds the profile file."""

    profile_file: Path
    """The ``.md`` itself."""

    source: str
    """``builtin``, ``project`` or ``global``; decides the shadowing order."""

    permissions: ToolPermissions = field(default_factory=ToolPermissions)
    """What the profile may call. Unrestricted when the file says nothing."""

    @property
    def label(self) -> str:
        """Display name: capitalized, with the dashes turned into spaces."""
        return self.name.replace('-', ' ').title()
