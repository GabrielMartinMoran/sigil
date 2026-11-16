"""The type_of helper of the agents layer."""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelMessage, UserPromptPart

from src.core.agents.origin import origin

KEY = 'sigil_message_types'


def type_of(message: ModelMessage, part: Any) -> dict[str, Any]:
    """Untyped historical parts are user input, even if they resemble a callback."""
    entries = (getattr(message, 'metadata', None) or {}).get(KEY, {})
    return entries.get(part.timestamp.isoformat(), origin()) if isinstance(part, UserPromptPart) else origin()
