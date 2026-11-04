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

if TYPE_CHECKING:
    from pydantic_ai.toolsets import AbstractToolset

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
        instructions=_load_prompt(),
        end_strategy='exhaustive',
        # Un error de tool vuelve al modelo como resultado fallido en vez de
        # abortar el turno; los fallos transitorios del proveedor se reintentan.
        capabilities=[model_retry, ToolErrorRecovery()],
        # Toolsets externos (p. ej. servidores MCP); las tools de funciones se
        # siguen registrando después con `ToolManager`.
        toolsets=list(toolsets or []),
    )

    if skill_registry is not None:
        # Progressive disclosure, nivel 1: los metadatos (nombre + descripción)
        # de los skills instalados se recalculan en cada request. Así un skill
        # agregado durante la sesión aparece sin reiniciar el agente.
        @agent.instructions(name='skills')
        def _skills_metadata() -> str:
            return skill_registry.render_available_skills()

    return agent
