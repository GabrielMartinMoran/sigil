"""One row of the agent picker."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.agents.profile.models.agent_profile import AgentProfile


@dataclass(frozen=True, slots=True)
class AgentPickerRow:
    """Display data only: remote profiles do not imply local filesystem paths."""

    name: str
    description: str
    source: str
    permissions: str

    @property
    def label(self) -> str:
        return self.name.replace('-', ' ').replace('_', ' ').title()

    @classmethod
    def from_profile(cls, profile: AgentProfile) -> AgentPickerRow:
        return cls(profile.name, profile.description, profile.source, profile.permissions.describe())
