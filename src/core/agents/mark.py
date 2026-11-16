"""The mark helper of the agents layer."""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelRequest, UserPromptPart

from src.core.agents.type_of import KEY


def mark(message: ModelRequest, part: UserPromptPart, value: dict[str, Any]) -> None:
    existing = (message.metadata or {}).get(KEY, {})
    if existing.get(part.timestamp.isoformat()) == value:
        return
    metadata = dict(message.metadata or {})
    metadata[KEY] = {**metadata.get(KEY, {}), part.timestamp.isoformat(): value}
    message.metadata = metadata
