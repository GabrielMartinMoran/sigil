from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from pydantic_ai import Agent, RunContext

from src.engine.agents.profile.capability import ToolPermissionsCapability
from src.engine.agents.profile.rendering import render_agent_profile
from src.engine.agents.profile.source import ProfileSource
from src.engine.agents.prompts.render import render
from src.engine.agents.retry import RetryTransientModelErrors
from src.engine.agents.tool_errors import ToolErrorRecovery
from src.engine.agents.tree.prompt_tail import AgentContextTail
from src.engine.agents.tree.rendering import render_subagents
from src.engine.agents.tree.summoner import SigilDeps
from src.engine.providers.factory.unconfigured import UnconfiguredModel
from src.engine.settings import DEFAULT_MODEL_MAX_ATTEMPTS
from src.engine.skills.loaded import loaded_resources, loaded_skills
from src.engine.skills.rendering import render_available_skills, render_loaded_skills

if TYPE_CHECKING:
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.models import Model
    from pydantic_ai.toolsets import AbstractToolset

    from src.engine.agents.profile.registry import AgentRegistry
    from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
    from src.engine.context.scope import ScopeTracker
    from src.engine.skills.registry import SkillRegistry

_SIGIL_PROMPT = os.path.join(os.path.dirname(__file__), 'prompts', 'sigil.md')


def _load_prompt() -> str:
    """The base prompt template, markers and all."""
    with open(_SIGIL_PROMPT, 'r', encoding='utf-8') as f:
        return f.read()


def create_agent(
    skill_registry: SkillRegistry | None = None,
    *,
    model: Model | None = None,
    toolsets: Sequence[AbstractToolset] | None = None,
    model_retry: RetryTransientModelErrors | None = None,
    workspace: WorkspaceInstructions | None = None,
    scope: ScopeTracker | None = None,
    agent_registry: AgentRegistry | None = None,
    profile: ProfileSource | None = None,
    extra_capabilities: Sequence[AbstractCapability] | None = None,
    available_features: Callable[[], frozenset[str]] | None = None,
    node_blocks: Callable[[str | None], str] | None = None,
) -> Agent:
    """Builds the agent.

    Args:
        model: What the agent talks to. The provider layer supplies a
            ``ModelRouter`` that the service swaps between turns; when nothing is
            configured yet it is an ``UnconfiguredModel`` that refuses to run with
            an actionable message instead of failing at import time.
        agent_registry: The agent profiles. The active one contributes an
            instruction block and decides which tools exist. It is also what the
            subagent block is listed from.
        profile: The profile of *this* agent, when it is not the active one of the
            registry. A subagent pins the profile that summoned it for its whole
            run; the root leaves it out and follows `/agent`. When given, the
            registry is only used to know which subagents exist.
        node_blocks: Request-only context for one execution: the agents around it
            and informational notices. It receives the node id of the run — ``None``
            for the root — and is rendered after the history on each request.
    """
    # Provider 502/429/timeouts are retried before bringing the turn down.
    if model_retry is None:
        model_retry = RetryTransientModelErrors(max_attempts=DEFAULT_MODEL_MAX_ATTEMPTS)

    source = (
        profile
        if profile is not None
        else (None if agent_registry is None else ProfileSource.from_registry(agent_registry))
    )

    capabilities: list[AbstractCapability] = [model_retry, ToolErrorRecovery()]
    if source is not None:
        # Not an `append` block: switching agent replaces the block, and the
        # previous profile's instructions have to stop travelling with the
        # conversation. It goes right after the base prompt, before everything
        # that describes the session.
        capabilities.append(ToolPermissionsCapability(source=source))

    if node_blocks is not None:
        capabilities.append(AgentContextTail(node_blocks=node_blocks))

    prompt_template = _load_prompt()

    def render_prompt() -> str:
        """The prompt for the request about to be made."""
        return render(prompt_template, None if available_features is None else available_features())

    agent = Agent(
        model if model is not None else UnconfiguredModel(),
        name='Sigil',
        # Every run knows the node it belongs to. The root's run carries `None` and
        # resolves the session's root node from the tree, which is what lets `/load`
        # swap a session under a long-lived agent.
        deps_type=SigilDeps,
        description='A helpful coding assistant.',
        # Rendered once per request against what the model can see. It still goes
        # first, so the prefix stays cacheable while nothing changes.
        instructions=render_prompt,
        end_strategy='exhaustive',
        # A tool error goes back to the model as a failed result instead of
        # aborting the turn; transient provider failures are retried.
        capabilities=[*capabilities, *(extra_capabilities or [])],
        # External toolsets (e.g. MCP servers); function tools are still
        # registered afterwards with `ToolManager`.
        toolsets=list(toolsets or []),
    )

    if source is not None:
        # The profile has to be able to set the behaviour, so its block goes
        # before the ones that only describe the workspace.
        @agent.instructions(name='agent-profile')
        def _agent_profile() -> str:
            return render_agent_profile(source)

    if agent_registry is not None and source is not None:
        # Right after the profile: which subagents it may start is part of what the
        # profile is, and it changes when the folder does, so it is rebuilt per
        # request like the body above.
        @agent.instructions(name='subagents')
        def _subagents() -> str:
            return render_subagents(agent_registry, source.profile())

    if workspace is not None and scope is not None:
        # Replace the full workspace snapshot when the scope or files change.
        @agent.instructions(name='workspace')
        def _workspace_instructions() -> str:
            return workspace.render(scope.scopes())

    if skill_registry is not None:
        # Level 2 as a block, not as a tool result: the SKILL.md body survives
        # compaction and does not compete for the history budget. `rewrite` (the
        # default) and not `append`: loading a skill is rare and the body is large,
        # so duplicating it in the history costs more than the one-time cache miss.
        #
        # The names are derived from the history of *this* run: a subagent does not
        # inherit the skills its parent loaded, because a body is instructions and
        # the instructions of a different task do not belong in this one. Deriving
        # them is what makes a resumed session keep the skills it was following —
        # a registry in memory lost them on restart and said nothing.
        @agent.instructions(name='loaded-skills')
        def _loaded_skills(ctx: RunContext[SigilDeps]) -> str:
            return render_loaded_skills(skill_registry, loaded_skills(ctx.messages), loaded_resources(ctx.messages))

    if skill_registry is not None:
        # Progressive disclosure, level 1: the metadata (name + description)
        # of the installed skills is recomputed on every request. That way a skill
        # added during the session shows up without restarting the agent.
        @agent.instructions(name='skills')
        def _skills_metadata() -> str:
            return render_available_skills(skill_registry)

    return agent
