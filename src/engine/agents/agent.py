from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic_ai import Agent

from src.engine.agents.retry import RetryTransientModelErrors
from src.engine.agents.tool_errors import ToolErrorRecovery
from src.engine.providers.factory.unconfigured import UnconfiguredModel
from src.engine.settings import DEFAULT_MODEL_MAX_ATTEMPTS
from src.engine.skills.rendering import render_available_skills, render_loaded_skills

if TYPE_CHECKING:
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.models import Model
    from pydantic_ai.toolsets import AbstractToolset

    from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
    from src.engine.context.scope import ScopeTracker
    from src.engine.skills.loaded import LoadedSkills
    from src.engine.skills.registry import SkillRegistry

_SIGIL_PROMPT = os.path.join(os.path.dirname(__file__), 'prompts', 'sigil.md')


def _load_prompt() -> str:
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
    loaded_skills: LoadedSkills | None = None,
    extra_capabilities: Sequence[AbstractCapability] | None = None,
) -> Agent:
    """Builds the agent.

    Args:
        model: What the agent talks to. The provider layer supplies a
            ``ModelRouter`` that the service swaps between turns; when nothing is
            configured yet it is an ``UnconfiguredModel`` that refuses to run with
            an actionable message instead of failing at import time.
    """
    # Provider 502/429/timeouts are retried before bringing the turn down.
    if model_retry is None:
        model_retry = RetryTransientModelErrors(max_attempts=DEFAULT_MODEL_MAX_ATTEMPTS)

    agent = Agent(
        model if model is not None else UnconfiguredModel(),
        name='Sigil',
        description='A helpful coding assistant.',
        # Static block: it goes first so the prefix stays cacheable.
        instructions=_load_prompt(),
        end_strategy='exhaustive',
        # A tool error goes back to the model as a failed result instead of
        # aborting the turn; transient provider failures are retried.
        capabilities=[model_retry, ToolErrorRecovery(), *(extra_capabilities or [])],
        # External toolsets (e.g. MCP servers); function tools are still
        # registered afterwards with `ToolManager`.
        toolsets=list(toolsets or []),
    )

    if workspace is not None and scope is not None:
        # `on_change='append'`: the scope widens mid-turn when a
        # tool enters a new subdirectory. Appending the child text
        # preserves the instruction prefix; rewriting it would break the cache.
        @agent.instructions(name='workspace', on_change='append')
        def _workspace_instructions() -> str:
            return workspace.render(scope.scopes())

    if skill_registry is not None and loaded_skills is not None:
        # Level 2 as a block, not as a tool result: the SKILL.md body
        # survives compaction and does not compete for the history
        # budget. `rewrite` (the default) and not `append`: loading a skill is
        # rare and the body is large, so duplicating it in the history costs
        # more than the one-time cache miss.
        @agent.instructions(name='loaded-skills')
        def _loaded_skills() -> str:
            return render_loaded_skills(skill_registry, loaded_skills.names())

    if skill_registry is not None:
        # Progressive disclosure, level 1: the metadata (name + description)
        # of the installed skills is recomputed on every request. That way a skill
        # added during the session shows up without restarting the agent.
        @agent.instructions(name='skills')
        def _skills_metadata() -> str:
            return render_available_skills(skill_registry)

    return agent
