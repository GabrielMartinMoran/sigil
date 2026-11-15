"""What the facade and a session runtime both name, without owning each other.

Nothing here holds execution state or reads the store. The module exists so
``runtime`` and ``session_runtime`` can share the same constants, the same value
objects and the same pure helpers while their import stays one-way:

    runtime -> session_runtime -> runtime_support

The values moved here unchanged from ``src/server/runtime.py``. ``runtime.py``
re-exports them, so an importer that already names them there keeps working.
"""

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import BinaryContent, ModelMessage, ModelRequest, UserPromptPart

from src.engine.attachments.images import MAX_IMAGE_BYTES, ImageError, prepare_image
from src.engine.context.media_compatibility import PORTABLE_IMAGE_TYPES
from src.engine.interaction.models.question_answer import QuestionAnswer
from src.engine.interaction.models.question_item import QuestionItem
from src.engine.storage.accepted_inputs import AcceptedInput, ImageInput

OwnershipGuard = Callable[[], None]
"""Validates that this process still owns the store, or raises ``OwnershipError``.

The session runtime calls it before starting the queue consumer and the MCP
loader. It is a callback and not an object because the lock is process state:
the session never acquires or releases it.
"""

CHECKPOINT_INTERVAL = 1.0
"""Minimum seconds between checkpoints during a turn. The final save is mandatory."""

QUEUE_POLL_INTERVAL = 0.25
"""Bounded wait for an input accepted outside this runtime. Not a busy loop."""

IDLE_POLL_INTERVAL = 0.5
"""Seconds between two looks at "is there any work left".

A timer, not a busy loop: the question is one indexed row lookup, and the whole
point of the shutdown is that answering it costs less than staying alive.
"""

WAKE_PROMPT = 'A subagent finished while the session was idle. Read its report and continue.'
"""What the turn says when a finished subagent is why it exists.

It is the terminal's sentence, word for word (`ui.subagent_reports.WAKE_PROMPT`):
the chat separates a real report from a typed message by matching this text, so a
second wording here would paint a woken turn as something the user wrote. A test
pins the two together rather than importing across the boundary — the service does
not depend on the client, and that direction is what keeps it a service.
"""

DEFAULT_IDLE_GRACE: float | None = None
"""No automatic idle shutdown unless explicitly configured."""

QUESTION_TIMEOUT_SECONDS: float | None = None
"""Questions wait indefinitely unless an explicit timeout is configured."""

QUESTION_TIMEOUT_ENV = 'SIGIL_QUESTION_TIMEOUT'
"""Environment override for :data:`QUESTION_TIMEOUT_SECONDS`, in seconds."""

SLOW_TURN_NOTICE_SECONDS = 60.0
"""Seconds a turn may publish nothing before the service says it is still waiting.

A sign, not a limit: nothing is cancelled when it passes, and the notice is published
once per turn. A model that takes minutes is legitimate — what a person cannot see is
that the turn is alive — so the answer to silence is a sentence, never a deadline.
"""

PREVIEW_CHARS = 400
"""How much of a tool result travels in the event. The full text is in the transcript."""

MAX_IMAGES = 10
"""Pictures one message may carry.

The number is a ceiling on what a single turn is worth in tokens and bandwidth, and
it is checked before anything is written: a rejected message must not leave a row.
"""


def resolve_question_timeout(
    value: float | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> float | None:
    """The question deadline: the argument, then the environment, then no deadline.

    Raises:
        ValueError: when the configured value is not a positive number of seconds. A
            typo has to be reported where it was made: falling back to the default
            would hide a deadline the operator believes is set.
    """
    if value is not None:
        return _checked_timeout(value, 'question_timeout')
    source = os.environ if env is None else env
    raw = source.get(QUESTION_TIMEOUT_ENV, '').strip()
    if not raw:
        return QUESTION_TIMEOUT_SECONDS
    try:
        seconds = float(raw)
    except ValueError as exc:
        raise ValueError(f'{QUESTION_TIMEOUT_ENV} must be a number of seconds; got {raw!r}') from exc
    return _checked_timeout(seconds, QUESTION_TIMEOUT_ENV)


def _checked_timeout(seconds: float, source: str) -> float:
    """The deadline if it can bound a wait, refused loudly if it cannot."""
    if not seconds > 0:
        raise ValueError(f'{source} must be greater than zero seconds; got {seconds!r}')
    return seconds


@dataclass(frozen=True, slots=True)
class IncomingImage:
    """One image as the client sent it: the type it declared and base64 bytes.

    Nothing here is ever a path. A client sends the picture itself, never a filename
    the server would open, and the declared type is only a claim: the bytes are
    decoded and re-encoded before they are trusted.
    """

    media_type: str
    data: str


def decode_images(images: Sequence[IncomingImage]) -> list[ImageInput]:
    """Validates and normalizes the ordered images of one message.

    The order is the identity: the position of an image in the list is the
    ``[Image #n]`` number in the text, and the bytes are what the fingerprint
    covers, so a retry of the same message must send the same list in the same
    order.

    The bytes are decoded and re-encoded by `prepare_image`, so what is stored is
    bounded in dimensions and in size and carries the media type the picture
    actually is, not the one the client claimed.

    Raises:
        ValueError: when a message carries too many images, a declared type is not
            portable, a payload is not base64, an image is over the byte ceiling, or
            the bytes are not a picture that can be decoded. Nothing is written
            before the whole list is validated.
    """
    if len(images) > MAX_IMAGES:
        raise ValueError(f'A message carries at most {MAX_IMAGES} images.')
    return [_decode_image(number, image) for number, image in enumerate(images, start=1)]


def _decode_image(number: int, image: IncomingImage) -> ImageInput:
    """One image of the list, or the reason it cannot be accepted."""
    media_type = image.media_type.strip().lower()
    if media_type not in PORTABLE_IMAGE_TYPES:
        raise ValueError(f'Image {number} has an unsupported media type: {media_type or "missing"}.')
    try:
        raw = base64.b64decode(image.data, validate=True)
    except ValueError as exc:  # binascii.Error is a ValueError: bad alphabet, bad padding
        raise ValueError(f'Image {number} is not valid base64.') from exc
    if not raw:
        raise ValueError(f'Image {number} is empty.')
    # Before decoding the picture: a payload this large is refused without asking
    # Pillow to read it, which is what makes the ceiling cheap to enforce.
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(f'Image {number} is larger than {MAX_IMAGE_BYTES // (1024 * 1024)} MiB.')
    try:
        prepared = prepare_image(raw)
    except ImageError as exc:
        raise ValueError(f'Image {number} could not be read: {exc}') from exc
    return ImageInput(prepared.data, prepared.media_type)


@dataclass
class PendingQuestion:
    """A question asked by the agent, waiting for an answer from a browser."""

    id: str
    questions: list[QuestionItem]
    future: asyncio.Future[list[QuestionAnswer] | None] = field(
        default_factory=lambda: asyncio.get_event_loop().create_future()
    )


def prompt_matches(part: UserPromptPart, item: AcceptedInput) -> bool:
    """Whether a history prompt carries exactly the accepted text and images."""
    content = part.content
    if not item.images:
        return content == item.text
    if isinstance(content, str):
        return False
    values = list(content)
    if not values or values[0] != item.text:
        return False
    images = [value for value in values[1:] if isinstance(value, BinaryContent)]
    if len(images) != len(item.images):
        return False
    return all(
        image.data == stored.data and image.media_type == stored.media_type
        for image, stored in zip(images, item.images, strict=True)
    )


def matching_message(messages: Sequence[ModelMessage], item: AcceptedInput) -> ModelMessage | None:
    """The history message that carries an accepted input, if it is there yet."""
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, UserPromptPart) and prompt_matches(part, item):
                return message
    return None


def jsonable(value: Any) -> Any:
    """Tool arguments as JSON, with bytes summarized instead of base64'd."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return f'<{len(value)} bytes>'
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return str(value)[:PREVIEW_CHARS]


__all__ = [
    'CHECKPOINT_INTERVAL',
    'DEFAULT_IDLE_GRACE',
    'IDLE_POLL_INTERVAL',
    'MAX_IMAGES',
    'PREVIEW_CHARS',
    'QUESTION_TIMEOUT_ENV',
    'QUESTION_TIMEOUT_SECONDS',
    'QUEUE_POLL_INTERVAL',
    'SLOW_TURN_NOTICE_SECONDS',
    'WAKE_PROMPT',
    'IncomingImage',
    'OwnershipGuard',
    'PendingQuestion',
    'decode_images',
    'jsonable',
    'matching_message',
    'prompt_matches',
    'resolve_question_timeout',
]
