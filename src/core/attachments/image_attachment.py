"""One image pasted into the composer, from the clipboard to the provider.

This is the object the rest of the application passes around, and it is
deliberately small: the bytes the model will read, the media type it will hear,
and the size it will be billed for. Where the bytes came from — a screenshot, a
file manager, a saved PNG — is already history by the time this exists.

Why the number matters: `[Image #n]` in the message text is the only link the
model has between a sentence and a picture. The number is assigned when the image
is pasted, and the content list is built in that same order, so a caption that
says "the second one" and the second image are the same image.

The cost model lives alongside the type it prices, so `images` can build an
attachment without importing anything from the UI and the meter can ask one
object what an image costs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic_ai.messages import BinaryContent, BinaryImage

from src.core.attachments.image_metadata import image_tokens, new_image_id


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

    image_id: str = field(default_factory=new_image_id)
    """A short id, unique in the session, that survives the session file.

    It is not `[Image #n]`: that marker is per turn and repeats, so two pictures
    pasted in two turns share `[Image #1]`. This id is what the model is given when
    the pixels stop travelling, and what `view_image` takes to bring them back.
    """

    @property
    def token(self) -> str:
        """The marker that stands for the image in the message text."""
        return f'[Image #{self.number}]'

    @property
    def estimated_tokens(self) -> int:
        """What the image costs in the context meter."""
        return image_tokens(self.width, self.height)

    def to_content(self) -> BinaryContent:
        """The pydantic-ai content part the model receives.

        The `identifier` travels with the part and is persisted with the session,
        so the same image can be named in a later turn without the bytes.
        """
        return BinaryImage(
            data=self.data,
            media_type=self.media_type,  # type: ignore[arg-type]
            identifier=self.image_id,
        )

    def describe(self) -> str:
        """``640×480 PNG``: what the chat prints next to a pasted image."""
        return f'{self.width}×{self.height} {self.media_type.rsplit("/", 1)[-1].upper()}'
