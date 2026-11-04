from __future__ import annotations

import os
from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from src.config_provider import ConfigProvider
from src.engine.agents.retry import RetryTransientModelErrors
from src.engine.agents.tool_errors import ToolErrorRecovery
from src.engine.skills.rendering import render_available_skills, render_loaded_skills

if TYPE_CHECKING:
    from pydantic_ai.capabilities import AbstractCapability
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
    toolsets: Sequence[AbstractToolset] | None = None,
    model_retry: RetryTransientModelErrors | None = None,
    workspace: WorkspaceInstructions | None = None,
    scope: ScopeTracker | None = None,
    loaded_skills: LoadedSkills | None = None,
    extra_capabilities: Sequence[AbstractCapability] | None = None,
) -> Agent:
    model = OpenRouterModel(
        model_name=ConfigProvider.LLM_MODEL, provider=OpenRouterProvider(api_key=ConfigProvider.OPENROUTER_API_KEY)
    )

    # Los 502/429/timeouts del proveedor se reintentan antes de tumbar el turno.
    if model_retry is None:
        model_retry = RetryTransientModelErrors(max_attempts=ConfigProvider.MODEL_MAX_ATTEMPTS)

    agent = Agent(
        model,
        name='Sigil',
        description='A helpful coding assistant.',
        # Bloque estático: va primero para que el prefijo sea cacheable.
        instructions=_load_prompt(),
        end_strategy='exhaustive',
        # Un error de tool vuelve al modelo como resultado fallido en vez de
        # abortar el turno; los fallos transitorios del proveedor se reintentan.
        capabilities=[model_retry, ToolErrorRecovery(), *(extra_capabilities or [])],
        # Toolsets externos (p. ej. servidores MCP); las tools de funciones se
        # siguen registrando después con `ToolManager`.
        toolsets=list(toolsets or []),
    )

    if workspace is not None and scope is not None:
        # `on_change='append'`: el scope se amplía a mitad de turno cuando una
        # tool entra en un subdirectorio nuevo. Agregar el texto del hijo
        # preserva el prefijo de instrucciones; reescribirlo rompería el cache.
        @agent.instructions(name='workspace', on_change='append')
        def _workspace_instructions() -> str:
            return workspace.render(scope.scopes())

    if skill_registry is not None and loaded_skills is not None:
        # Nivel 2 como bloque, no como tool result: el cuerpo del SKILL.md
        # sobrevive a la compactación y no compite por el presupuesto del
        # historial. `rewrite` (el default) y no `append`: cargar un skill es
        # raro y el cuerpo es grande, así que duplicarlo en el historial cuesta
        # más que el cache miss de una sola vez.
        @agent.instructions(name='loaded-skills')
        def _loaded_skills() -> str:
            return render_loaded_skills(skill_registry, loaded_skills.names())

    if skill_registry is not None:
        # Progressive disclosure, nivel 1: los metadatos (nombre + descripción)
        # de los skills instalados se recalculan en cada request. Así un skill
        # agregado durante la sesión aparece sin reiniciar el agente.
        @agent.instructions(name='skills')
        def _skills_metadata() -> str:
            return render_available_skills(skill_registry)

    return agent
