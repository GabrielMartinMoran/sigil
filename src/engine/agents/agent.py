import os

from pydantic_ai import Agent
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider

from src.config_provider import ConfigProvider

_SIGIL_PROMPT = os.path.join(os.path.dirname(__file__), 'prompts', 'sigil.md')


def create_agent() -> Agent:
    model = OpenRouterModel(
        model_name=ConfigProvider.LLM_MODEL,
        provider=OpenRouterProvider(api_key=ConfigProvider.OPENROUTER_API_KEY),
    )

    agent = Agent(
        model,
        name='Sigil',
        description='A helpful coding assistant.',
        instructions=_SIGIL_PROMPT,
    )
    return agent
