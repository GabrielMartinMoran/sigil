"""Validated model preferences for agent profiles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.engine.agents.profile.models.agent_name import is_valid_agent_name
from src.engine.providers.domain.thinking import ThinkingChoice, parse_choice


@dataclass(frozen=True, slots=True)
class AgentModelConfig:
    account: str
    model: str
    effort: ThinkingChoice


def parse_agent_models(raw: object, path: Path) -> tuple[dict[str, AgentModelConfig], set[str], list[str]]:
    if not isinstance(raw, dict):
        return {}, set(), [f'{path}: `agents` must be an object mapping agent names to model settings.']
    agents: dict[str, AgentModelConfig] = {}
    errors: list[str] = []
    invalid: set[str] = set()
    for name, value in raw.items():
        label = f'{path}: agents.{name}'
        if not isinstance(name, str) or not is_valid_agent_name(name):
            errors.append(f'{label}: invalid agent name.')
            continue
        invalid.add(name)
        if not isinstance(value, dict) or set(value) != {'account', 'model', 'effort'}:
            errors.append(f'{label}: expected exactly `account`, `model`, and `effort`.')
            continue
        account, model, effort = value['account'], value['model'], value['effort']
        if not isinstance(account, str) or not account.strip() or not isinstance(model, str) or not model.strip():
            errors.append(f'{label}: `account` and `model` must be non-empty strings.')
            continue
        choice = parse_choice(effort) if isinstance(effort, str) else None
        if choice is None:
            errors.append(f'{label}: invalid `effort`.')
            continue
        agents[name] = AgentModelConfig(account.strip(), model.strip(), choice)
        invalid.remove(name)
    return agents, invalid, errors
