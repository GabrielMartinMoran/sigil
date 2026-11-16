"""The AgentFactory value of the agents layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.core.agents.agent import create_agent
from src.core.agents.agent_spec import AgentSpec
from src.core.agents.profile.profile_source import ProfileSource

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from src.core.agents.profile.models.agent_profile import AgentProfile


@dataclass
class AgentFactory:
    """Builds (and reuses) the agent of a profile."""

    spec: AgentSpec
    _agents: dict[str, Agent[Any, Any]] = field(default_factory=dict, repr=False)

    def clear_profiles(self) -> None:
        """Discard agents built from profiles of the previous workspace."""
        self._agents.clear()

    def root(self) -> Agent[Any, Any]:
        """The agent of the session: it follows `/agent`, so it is not cached."""
        return self._build(profile=None)

    def for_profile(self, profile: AgentProfile) -> Agent[Any, Any]:
        """The agent of a subagent profile, built once per profile."""
        cached = self._agents.get(profile.name)
        if cached is not None:
            return cached
        agent = self._build(profile=profile)
        self._agents[profile.name] = agent
        return agent

    def _build(self, *, profile: AgentProfile | None) -> Agent[Any, Any]:
        features = self.spec.available_features
        agent = create_agent(
            self.spec.skill_registry,
            model=self.spec.model,
            toolsets=list(self.spec.toolsets),
            model_retry=self.spec.model_retry,
            workspace=self.spec.workspace,
            scope=self.spec.scope,
            agent_registry=self.spec.agent_registry,
            profile=None if profile is None else ProfileSource.fixed(profile),
            extra_capabilities=self.spec.extra_capabilities,
            available_features=None
            if features is None
            else _bound(features, None if profile is None else profile.name),
            node_blocks=self.spec.node_blocks,
            allow_external_files=self.spec.allow_external_files,
            root=self.spec.root,
        )
        for register in self.spec.tool_registrars:
            register(agent)
        return agent


def _bound(features: Callable[[str | None], frozenset[str]], agent_name: str | None) -> Callable[[], frozenset[str]]:
    """The per-request prompt renderer, with the profile already decided."""
    return lambda: features(agent_name)
