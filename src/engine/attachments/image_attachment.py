"""One image pasted into the composer, from the clipboard to the provider.

This is the object the rest of the application passes around, and it is
deliberately small: the bytes the model will read, the media type it will hear,
and the size it will be billed for. Where the bytes came from — a screenshot, a
file manager, a saved PNG — is already history by the time this exists.

Why the number matters: `[Image #n]` in the message text is the only link the
model has between a sentence and a picture. The number is assigned when the image
is pasted, and the content list is built in that same order, so a caption that
says "the second one" and the second image are the same image.

The cost model lives here, next to the type it prices, so `images` can build an
attachment without importing anything from the UI and the meter can ask one
object what an image costs.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.messages import BinaryContent, BinaryImage

PATCH = 28
"""Side of the square a provider divides the image into before counting tokens."""

BASE_IMAGE_TOKENS = 1
"""The floor: one patch is the cheapest an image can be."""


def image_tokens(width: int, height: int) -> int:
    """Estimated tokens for an image of ``width``×``height`` pixels.

    Patches, not bytes: a 40 KB screenshot of a dense table costs the same as a
    4 MB photograph of the same size, because the model looks at the same number
    of pixels either way.
    """
    if width <= 0 or height <= 0:
        return BASE_IMAGE_TOKENS
    across = -(-width // PATCH)
    down = -(-height // PATCH)
    return max(BASE_IMAGE_TOKENS, across * down)


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    """A prepared image, ready to travel with the prompt."""

    number: int
    """Position in the turn, 1-based. It is the number the token shows."""

    data: bytes
    """The bytes exactly as they will be sent: already re-encoded if needed."""

    media_type: str
    width: int
    height: int

    @property
    def token(self) -> str:
        """The marker that stands for the image in the message text."""
        return f'[Image #{self.number}]'

    @property
    def estimated_tokens(self) -> int:
        """What the image costs in the context meter."""
        return image_tokens(self.width, self.height)

    def to_content(self) -> BinaryContent:
        """The pydantic-ai content part the model receives."""
        return BinaryImage(
            data=self.data,
            media_type=self.media_type,  # type: ignore[arg-type]
            identifier=self.token,
        )

    def describe(self) -> str:
        """``640×480 PNG``: what the chat prints next to a pasted image."""
        return f'{self.width}×{self.height} {self.media_type.rsplit("/", 1)[-1].upper()}'
