"""The AgentSpec value of the agents layer."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai import Agent
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.models import Model
    from pydantic_ai.toolsets import AbstractToolset

    from src.core.agents.profile.agent_registry import AgentRegistry
    from src.core.agents.retry_transient_model_errors import RetryTransientModelErrors
    from src.core.context.instructions.workspace_instructions import WorkspaceInstructions
    from src.core.context.scope import ScopeTracker
    from src.core.skills.skill_registry import SkillRegistry


ToolRegistrarCallable = Callable[['Agent[Any, Any]'], None]


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
    allow_external_files: Callable[[], bool] | None = None
    root: Callable[[], Path] | None = None
    available_features: Callable[[str | None], frozenset[str]] | None = None
    """What the model will see, by agent name. ``None`` means the active profile.

    A subagent's profile is not the active one of the registry, so a prompt built
    for the root would name tools the subagent does not have. The callable receives
    the profile the prompt is for — ``None`` for the root, which follows `/agent` —
    and answers with the right set.
    """
