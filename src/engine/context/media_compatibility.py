"""Portable image formats at the provider boundary, without changing storage."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import cast

from pydantic_ai.messages import (
    BinaryContent,
    BinaryImage,
    ModelMessage,
    ModelRequestPart,
    ModelResponsePart,
    ToolReturnPart,
    UserPromptPart,
)

PORTABLE_IMAGE_TYPES = frozenset({'image/png', 'image/jpeg', 'image/gif', 'image/webp'})


def compatible_images(messages: Sequence[ModelMessage]) -> list[ModelMessage]:
    result: list[ModelMessage] = []
    for message in messages:
        parts: list[ModelRequestPart | ModelResponsePart] = []
        for part in message.parts:
            if isinstance(part, (UserPromptPart, ToolReturnPart)) and isinstance(part.content, (list, tuple)):
                original = cast(Sequence[object], part.content)
                content = [_portable(item) for item in original]
                if any(new is not old for new, old in zip(content, original)):
                    part = replace(part, content=content)
            parts.append(part)
        result.append(message if all(a is b for a, b in zip(parts, message.parts)) else replace(message, parts=parts))
    return result


def _portable(item: object) -> object:
    if not isinstance(item, BinaryContent) or not item.is_image or item.media_type in PORTABLE_IMAGE_TYPES:
        return item
    from src.engine.attachments.images import prepare_image

    image = prepare_image(item.data)
    return BinaryImage(data=image.data, media_type=image.media_type, identifier=item.identifier)
