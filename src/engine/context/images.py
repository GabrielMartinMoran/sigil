"""An image costs what its pixels cost, not what its `str()` happens to look like.

This module exists because the estimator was silently wrong for every image: the
content of a multimodal user prompt is a list, `_as_text` stringified each item,
and `str(BinaryContent)` is a forty-character repr — so a 4K screenshot was
counted as ten tokens and the meter believed the conversation fit when it did
not.

The estimate is a function of the encoded payload: the dimensions in the header,
then the same patch arithmetic the attachment used before it was sent. It needs no
decode, which matters because the meter walks the whole history on every step.
"""

from __future__ import annotations

import io
from typing import TypeGuard

from PIL import Image
from pydantic_ai.messages import BinaryContent

from src.engine.attachments.image_attachment import image_tokens


def image_size(data: bytes) -> tuple[int, int]:
    """Width and height of an encoded image, read from its header.

    Returns ``(0, 0)`` for a payload that cannot be read: the meter estimates, and
    an estimate is never a reason to fail a request.
    """
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.size
    except Exception:  # noqa: BLE001 - a corrupt payload must not break the estimate
        return 0, 0


def image_part_tokens(part: BinaryContent) -> int:
    """Tokens estimated for one binary image part."""
    width, height = image_size(part.data)
    return image_tokens(width, height)


def is_image_part(item: object) -> TypeGuard[BinaryContent]:
    """Whether ``item`` is a binary image, as opposed to a document or audio."""
    return isinstance(item, BinaryContent) and item.is_image
