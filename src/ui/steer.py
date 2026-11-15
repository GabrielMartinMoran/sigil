"""A message that steers the running turn."""

from __future__ import annotations

from dataclasses import dataclass, field

from textual.widgets import Static

from src.core.attachments.image_attachment import ImageAttachment


@dataclass
class Steer:
    """A message typed while a turn runs, waiting to be delivered to it.

    ``enqueue_id`` is ``None`` for the few milliseconds between the submit and
    the run being ready: the message is already accepted, and it is handed to the
    run as soon as it exists (see ``_flush_steering``).
    """

    text: str
    attachments: tuple[ImageAttachment, ...] = field(default=())
    widget: Static | None = None
    enqueue_id: str | None = None
    delivered: bool = field(default=False)

    def enqueued(self) -> tuple[str, ...]:
        """The message as ``enqueue`` wants it: text first, then each image.

        One argument per item, because ``enqueue`` gathers adjacent content into a
        single ``UserPromptPart``. A list passed as one argument is a list, not a
        multimodal prompt.
        """
        if not self.attachments:
            return (self.text,)
        ordered = sorted(self.attachments, key=lambda attachment: attachment.number)
        return (self.text, *(attachment.to_content() for attachment in ordered))
