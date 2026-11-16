"""The ConfigFileState value of the agents layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from src.core.agents.agent_model_config import AgentModelConfig


@dataclass(frozen=True, slots=True)
class ConfigFileState:
    """What one config file says about models, and what is wrong with it."""

    path: Path
    default: AgentModelConfig | None = None
    default_invalid: bool = False
    agents: Mapping[str, AgentModelConfig] = field(default_factory=dict)
    invalid_agents: frozenset[str] = frozenset()
    errors: tuple[str, ...] = ()

    def mentions(self, target: str | None) -> bool:
        """Whether this file says anything about ``target``, validly or not.

        It matters that an invalid entry still counts as a mention: a project file
        that sets an agent badly shadows the user's file instead of falling back to
        it, and the screen has to show that rather than a value the merge ignored.
        """
        if target is None:
            return self.default is not None or self.default_invalid
        return target in self.agents or target in self.invalid_agents

    def entry(self, target: str | None) -> AgentModelConfig | None:
        return self.default if target is None else self.agents.get(target)

    def is_invalid(self, target: str | None) -> bool:
        return self.default_invalid if target is None else target in self.invalid_agents
