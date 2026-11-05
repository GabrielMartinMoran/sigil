"""The image library of a session: short ids, and getting an image back.

An image costs what its pixels cost, so it cannot travel with every request
forever. What travels is its id; the bytes stay in the session log, which already
persists them, and a tool brings them back on demand.

The id is not the `[Image #n]` marker: that one is per turn and repeats, so with two
pictures pasted in two different turns `[Image #1]` names both.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic_ai.messages import BinaryContent, ModelMessage, ModelRequest, UserPromptPart

IMAGE_ID_PATTERN = re.compile(r'img_[0-9a-f]{6,}')
"""What an id this application mints looks like."""


def is_image_id(value: str) -> bool:
    """Whether ``value`` looks like an id this application minted."""
    return bool(IMAGE_ID_PATTERN.fullmatch(value.strip()))


def _images_by_id(messages: Sequence[ModelMessage]) -> dict[str, list[BinaryContent]]:
    """Every image in the history, grouped by its identifier, oldest first."""
    index: dict[str, list[BinaryContent]] = {}
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, UserPromptPart) or not isinstance(part.content, list):
                continue
            for item in part.content:
                if isinstance(item, BinaryContent) and item.is_image:
                    index.setdefault(item.identifier, []).append(item)
    return index


def image_ids(messages: Sequence[ModelMessage]) -> dict[str, BinaryContent]:
    """One image per id, the most recent winning.

    Legacy sessions saved `[Image #n]` as the identifier, which repeats across
    turns. The newest match is the best guess for an ambiguous id, and it keeps
    those sessions reloadable instead of failing outright.
    """
    return {key: images[-1] for key, images in _images_by_id(messages).items()}


def find_image(messages: Sequence[ModelMessage], image_id: str) -> BinaryContent | None:
    """The image an id names, or ``None`` when the session never carried it."""
    wanted = image_id.strip()
    if not wanted:
        return None
    return image_ids(messages).get(wanted)
