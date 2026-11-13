"""Validated model preferences for agent profiles and the session default."""

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


def _entry(value: object, label: str) -> tuple[AgentModelConfig | None, str | None]:
    """One `{account, model, effort}` block, or the reason it is not one."""
    if not isinstance(value, dict) or set(value) != {'account', 'model', 'effort'}:
        return None, f'{label}: expected exactly `account`, `model`, and `effort`.'
    account, model, effort = value['account'], value['model'], value['effort']
    if not isinstance(account, str) or not account.strip() or not isinstance(model, str) or not model.strip():
        return None, f'{label}: `account` and `model` must be non-empty strings.'
    choice = parse_choice(effort) if isinstance(effort, str) else None
    if choice is None:
        return None, f'{label}: invalid `effort`.'
    return AgentModelConfig(account.strip(), model.strip(), choice), None


def parse_agent_models(raw: object, path: Path) -> tuple[dict[str, AgentModelConfig], set[str], list[str]]:
    """The `agents` block: a model per profile, each one validated on its own."""
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
        config, error = _entry(value, label)
        if error is not None:
            errors.append(error)
            continue
        agents[name] = config  # type: ignore[assignment] - `error is None` means it parsed
        invalid.remove(name)
    return agents, invalid, errors


def parse_default_model(raw: object, path: Path) -> tuple[AgentModelConfig | None, list[str]]:
    """The `default_model` block: the model a conversation starts on.

    It has the shape of one `agents` entry on purpose — the same three keys, the same
    effort vocabulary — so a reader who has written one already knows the other.
    ``None`` with no error means the file does not set it, which is not a problem.
    """
    if raw is None:
        return None, []
    config, error = _entry(raw, f'{path}: default_model')
    if error is not None:
        return None, [error]
    return config, []
