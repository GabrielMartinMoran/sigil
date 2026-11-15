"""One row of the model defaults table."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.agents.model_defaults import ConfigScope
from src.core.agents.profile.models.agent_role import AgentRole


@dataclass(frozen=True, slots=True)
class DefaultsRow:
    """One target: the default for new conversations, or an installed profile."""

    target: str | None
    label: str
    summary: str
    sources: frozenset[ConfigScope] = frozenset()
    invalid: bool = False
    active: bool = False
    installed: bool = True
    role: AgentRole = AgentRole.AGENT
    """Which kind of profile this is, for the section it is listed under.

    ``installed`` and the role answer different questions: a name in the config with
    no profile behind it has no role, and it is the one row worth clearing.
    """
