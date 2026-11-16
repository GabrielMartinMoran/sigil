"""The AgentModelConfig value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.providers.domain.thinking import ThinkingChoice


@dataclass(frozen=True, slots=True)
class AgentModelConfig:
    account: str
    model: str
    effort: ThinkingChoice
