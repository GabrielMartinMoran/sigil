"""Turning a composed message into what the model receives.

The message the user typed already carries `[Image #1]` where each picture goes.
That text is not decoration to strip: it is the only thing that tells the model
which sentence belongs to which image, so it travels as it is, followed by the
images in the same order. A prompt that reads "mira [Image #1] y compará con
[Image #2]" arrives as that text plus two content parts, and the model can point
at either one.

The one rule that matters here: **the order is the number.** `ImageAttachment`
is numbered 1..n by the composer and the content list is built by that number, so
a mismatch between the text and the pictures is not possible from this side.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import BinaryContent, UserContent

from src.engine.attachments.image_attachment import ImageAttachment

TEXT_ONLY = ''
"""What a message with no images sends: just its text."""


def user_content(text: str, attachments: Sequence[ImageAttachment] = ()) -> str | list[UserContent]:
    """The prompt as pydantic-ai takes it.

    Returns:
        The text alone when there is nothing attached — a plain string, which is
        what every provider path already handles — or a list of the text and the
        images in order.
    """
    if not attachments:
        return text
    ordered = sorted(attachments, key=lambda attachment: attachment.number)
    content: list[UserContent] = [text]
    content.extend(attachment.to_content() for attachment in ordered)
    return content


def describe_attachments(attachments: Sequence[ImageAttachment]) -> str:
    """``2 images (640×480 PNG, 1568×882 PNG)`` for the chat line."""
    if not attachments:
        return ''
    sized = ', '.join(attachment.describe() for attachment in attachments)
    noun = 'image' if len(attachments) == 1 else 'images'
    return f'{len(attachments)} {noun} ({sized})'


def images_in(parts: Sequence[object]) -> list[BinaryContent]:
    """The binary image parts of a message, for the context meter and the chat."""
    return [part for part in parts if isinstance(part, BinaryContent) and part.is_image]
