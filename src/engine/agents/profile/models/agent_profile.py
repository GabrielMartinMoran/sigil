"""One agent profile: the frontmatter and where it came from.

The body is not here on purpose. Metadata is what the picker and the status bar
need on every repaint; the Markdown body is read once, when the profile becomes
active, and injected as an instruction block.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.models.channel import UNIDIRECTIONAL, ChannelPolicy
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

    role: AgentRole = AgentRole.AGENT
    """Whether the user can select this profile or only an agent can summon it."""

    max_steps: int | None = None
    """Model calls this profile gets in one run, or ``None`` for the global limit.

    Running out does not abort: the run gets one last call, with the tools taken
    away, to explain where it got and what is left.
    """

    channel: ChannelPolicy = UNIDIRECTIONAL
    """Which way messages may travel between this profile and its neighbours."""

    @property
    def label(self) -> str:
        """Display name: capitalized, with the separators turned into spaces.

        Both separators: a profile is named with dashes in some trees and with
        underscores in others, and `Pas_Developer` reads like a variable name.
        """
        return self.name.replace('-', ' ').replace('_', ' ').title()
