"""Building the agent object of a node, from everything the session already has.

The composition root assembles the session once: the model, the tools, the MCP
toolsets, the capabilities that project the context and gate the model features,
the workspace instructions. Every agent in the tree wants the same collection —
a subagent is an agent, so it has the same tools, the same model and the same
permissions mechanism — and the only thing that differs is *which profile* says
how to behave.

That is what this factory is: the shared half, held once, plus the per-profile
half, built on demand. It exists instead of a second assembly in the composition
root because the registrars are closures over live objects (the question hub, the
roadmap store, the MCP gateway) and a copy of that wiring in a second place would
drift the first time a tool is added.

Agents are cached per profile and reused. A run is per call — ``agent.iter()``
opens its own — so one agent object per profile is enough, and it is the same
reason the root agent is built once and reused across turns.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.engine.agents.agent import create_agent
from src.engine.agents.profile.source import ProfileSource

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.models import Model
    from pydantic_ai.toolsets import AbstractToolset

    from src.engine.agents.profile.models.agent_profile import AgentProfile
    from src.engine.agents.profile.registry import AgentRegistry
    from src.engine.agents.retry import RetryTransientModelErrors
    from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
    from src.engine.context.scope import ScopeTracker
    from src.engine.skills.registry import SkillRegistry

ToolRegistrarCallable = Callable[['Agent[Any, Any]'], None]
"""Registers one family of tools on an agent. ``register_file_tools`` and friends."""


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """The parts of the session every agent of the tree is built from."""

    model: Model
    skill_registry: SkillRegistry | None = None
    toolsets: Sequence[AbstractToolset[Any]] = ()
    model_retry: RetryTransientModelErrors | None = None
    workspace: WorkspaceInstructions | None = None
    scope: ScopeTracker | None = None
    agent_registry: AgentRegistry | None = None
    extra_capabilities: Sequence[AbstractCapability] = ()
    """Capabilities of the session, shared by every agent of the tree.

    The context projection and the model feature gate are session-scoped on
    purpose: the meter that budgets the next request and the window it measures
    against belong to the conversation, and a subagent's requests are part of what
    the conversation costs.
    """
    tool_registrars: Sequence[ToolRegistrarCallable] = ()
    node_blocks: Callable[[str | None], str] | None = None
    """The instructions of one *execution*: the agents around it.

    Per node and not per profile: one agent object serves every execution of its
    profile, and what it should be told depends on which node is running. The
    callable receives the node id — ``None`` for the root — and its result is
    rendered on every request, because the tree moves under it.
    """
    available_features: Callable[[str | None], frozenset[str]] | None = None
    """What the model will see, by agent name. ``None`` means the active profile.

    A subagent's profile is not the active one of the registry, so a prompt built
    for the root would name tools the subagent does not have. The callable receives
    the profile the prompt is for — ``None`` for the root, which follows `/agent` —
    and answers with the right set.
    """


@dataclass
class AgentFactory:
    """Builds (and reuses) the agent of a profile."""

    spec: AgentSpec
    _agents: dict[str, Agent[Any, Any]] = field(default_factory=dict, repr=False)

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
        )
        for register in self.spec.tool_registrars:
            register(agent)
        return agent


def _bound(features: Callable[[str | None], frozenset[str]], agent_name: str | None) -> Callable[[], frozenset[str]]:
    """The per-request prompt renderer, with the profile already decided."""
    return lambda: features(agent_name)


__all__ = ['AgentFactory', 'AgentSpec', 'ToolRegistrarCallable']
