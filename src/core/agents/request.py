"""The request helper of the agents layer."""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelRequest, UserPromptPart

from src.core.agents.mark import mark


def request(text: str, value: dict[str, Any]) -> ModelRequest:
    part = UserPromptPart(text)
    message = ModelRequest(parts=[part])
    mark(message, part, value)
    return message
