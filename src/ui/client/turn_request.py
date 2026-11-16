"""A message a client asks the service to run."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from src.ui.client.outgoing_image import OutgoingImage


@dataclass(frozen=True, slots=True)
class TurnRequest:
    """One message to send, with the identity a retry has to reuse.

    The id is generated here, on the client, because the queue's deduplication is
    keyed by it: a retry that let the service invent a second id would be a second
    message, which is the failure the durable queue exists to prevent. Retrying the
    same instance is therefore the correct retry, and a new instance is a new
    message.
    """

    text: str
    images: tuple[OutgoingImage, ...] = ()
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def payload(self) -> dict[str, Any]:
        """The body `/api/turn` takes, with the images in their order."""
        return {
            'text': self.text,
            'request_id': self.request_id,
            'images': [{'media_type': image.media_type, 'data': image.data} for image in self.images],
        }
