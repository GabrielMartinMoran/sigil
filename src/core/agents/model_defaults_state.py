"""The ModelDefaultsState value of the agents layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from src.core.agents.agent_model_config import AgentModelConfig
from src.core.agents.config_file_state import ConfigFileState
from src.core.agents.config_scope import ConfigScope

SCOPES: tuple[ConfigScope, ...] = (ConfigScope.PROJECT, ConfigScope.GLOBAL)


@dataclass(frozen=True, slots=True)
class ModelDefaultsState:
    """Both config files, in precedence order, as the screen needs them."""

    files: Mapping[ConfigScope, ConfigFileState]
    fixed: Path | None = None
    """The single explicit path, when ``SIGIL_CONFIG`` replaced the pair."""

    @property
    def destinations(self) -> tuple[ConfigScope, ...]:
        return tuple(scope for scope in SCOPES if scope in self.files)

    @property
    def errors(self) -> tuple[str, ...]:
        return tuple(error for scope in self.destinations for error in self.files[scope].errors)

    def path_for(self, scope: ConfigScope) -> Path:
        return self.files[scope].path

    def sources(self, target: str | None) -> frozenset[ConfigScope]:
        """The files that mention ``target``. One reading is enough for the screen."""
        return frozenset(scope for scope in self.destinations if self.files[scope].mentions(target))

    def effective(self, target: str | None) -> AgentModelConfig | None:
        """The entry that wins, or ``None`` when no file sets one."""
        for scope in self.destinations:
            file = self.files[scope]
            if file.mentions(target):
                return file.entry(target)
        return None

    def invalid(self, target: str | None) -> bool:
        """Whether the winning entry is one the files could not parse."""
        for scope in self.destinations:
            file = self.files[scope]
            if file.mentions(target):
                return file.is_invalid(target)
        return False

    def agent_names(self) -> tuple[str, ...]:
        """Every agent either file mentions, including the badly written ones."""
        names = set()
        for scope in self.destinations:
            file = self.files[scope]
            names.update(file.agents)
            names.update(file.invalid_agents)
        return tuple(sorted(names))
