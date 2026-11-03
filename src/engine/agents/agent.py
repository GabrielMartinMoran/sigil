from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from src.config_provider import ConfigProvider

if TYPE_CHECKING:
    from src.engine.skills.registry import SkillRegistry

_SIGIL_PROMPT = os.path.join(os.path.dirname(__file__), 'prompts', 'sigil.md')


def _load_prompt() -> str:
    with open(_SIGIL_PROMPT, 'r', encoding='utf-8') as f:
        return f.read()


def create_agent(skill_registry: SkillRegistry | None = None) -> Agent:
    model = OpenRouterModel(
        model_name=ConfigProvider.LLM_MODEL, provider=OpenRouterProvider(api_key=ConfigProvider.OPENROUTER_API_KEY)
    )

    agent = Agent(
        model,
        name='Sigil',
        description='A helpful coding assistant.',
        instructions=_load_prompt(),
        end_strategy='exhaustive',
    )

    if skill_registry is not None:
        # Progressive disclosure, nivel 1: los metadatos (nombre + descripción)
        # de los skills instalados se recalculan en cada request. Así un skill
        # agregado durante la sesión aparece sin reiniciar el agente.
        @agent.instructions(name='skills')
        def _skills_metadata() -> str:
            return skill_registry.render_available_skills()

    return agent
