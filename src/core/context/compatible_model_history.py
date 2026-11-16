"""The compatible_model_history helper of the context layer."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage


def compatible_model_history(messages: Sequence[ModelMessage], model_name: str | None) -> list[ModelMessage]:
    """Avoid replaying signed reasoning produced by a different model."""
    from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart

    result = []
    for message in messages:
        if (
            not isinstance(message, ModelResponse)
            or not model_name
            or not message.model_name
            or message.model_name == model_name
        ):
            result.append(message)
            continue
        parts = [part for part in message.parts if not (isinstance(part, ThinkingPart) and part.signature)]
        if parts:
            parts = [
                replace(part, id=None) if isinstance(part, (TextPart, ThinkingPart, ToolCallPart)) else part
                for part in parts
            ]
            result.append(replace(message, parts=parts, provider_response_id=None, history_digest=None))
    from src.core.context.media_compatibility import compatible_images

    return compatible_images(result)
