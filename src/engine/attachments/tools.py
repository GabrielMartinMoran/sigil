"""`view_image`: hand the model back a picture, from the session or from anywhere.

An image costs what its pixels cost, so only the most recent ones travel with the
request. The older ones leave their id behind, and this tool turns that id back into
the picture. It also loads a picture that never was in the session: a URL or a path.

Returning a `BinaryImage` from a tool is not the same as returning a link: pydantic-ai
maps multimodal tool results natively —Anthropic as an image block inside the
`tool_result`, OpenAI likewise— so the model really does see the image, not a path
to it. `ToolReturn` is what makes that possible: `return_value` is the sentence the
model reads as the answer, and `content` carries the pixels beside it.

Two rules keep it from becoming a way to burn the context:

- **The same source is loaded once per turn.** A picture the turn in progress
  already carries is not sent again; the answer says it is already attached. That is
  what stops a model from fetching the same screenshot on every step.
- **Ids are derived from the source**, so "the picture at this URL" is one id however
  many times it is asked for, and the cache can tell a copy from a distinct picture.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.messages import BinaryContent, BinaryImage, ModelMessage, ModelRequest

from src.engine.agents.turn_map import library_run_id
from src.engine.attachments.image_attachment import ImageAttachment
from src.engine.attachments.images import ImageError, prepare_image
from src.engine.attachments.library import find_image
from src.engine.files.models.file_tool_error import FileToolError
from src.engine.files.paths import resolve
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.compaction_policies import VIEW_IMAGE_COMPACTOR
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.permission import ToolGroup, ToolPermission
from src.engine.tooling.registrar import ToolRegistrar
from src.engine.web.download import DownloadError, download_image

TOOL_NAME = 'view_image'

MAX_IMAGE_BYTES = 8 * 1024 * 1024
"""Ceiling for a download, before it is decoded and re-encoded down to the limit."""

_VIEW = ToolPermission(groups=(ToolGroup.READ,), resource='source')
"""Reads a picture: from the session, from a path or from the network."""


def image_id_for(source: str) -> str:
    """A stable id for a picture named by ``source``.

    Deterministic on purpose: the same URL or path is the same picture, so asking for
    it twice does not mint a second id that the cache would charge for separately.
    """
    digest = hashlib.sha256(source.strip().encode('utf-8')).hexdigest()[:8]
    return f'img_{digest}'


def is_url(source: str) -> bool:
    """Whether ``source`` names something to fetch over the network."""
    parsed = urlparse(source.strip())
    return parsed.scheme in ('http', 'https') and bool(parsed.netloc)


def images_in_round(messages: Sequence[ModelMessage]) -> set[str]:
    """The ids of every picture the turn in progress already carries.

    Images travel in two places, and both count: a user prompt (the paste) and a tool
    result (what a previous `view_image` returned).
    """
    current = current_turn(messages)
    found: set[str] = set()
    for message in messages:
        if not isinstance(message, ModelRequest) or library_run_id(message) != current:
            continue
        for part in message.parts:
            if not isinstance(part.content, list):
                continue
            for item in part.content:
                if isinstance(item, BinaryContent) and item.is_image:
                    found.add(item.identifier)
    return found


def current_turn(messages: Sequence[ModelMessage]) -> str | None:
    """The round the latest message belongs to."""
    for message in reversed(messages):
        identifier = library_run_id(message)
        if identifier is not None:
            return identifier
    return None


def _from_session(messages: Sequence[ModelMessage], identifier: str) -> ImageAttachment:
    """A picture the session already carries, rebuilt from its bytes."""
    found = find_image(messages, identifier)
    if found is None:
        raise FileToolError(
            f'No image with id {identifier!r} in this session. The ids are the `img_...` '
            'values the messages mention, or a URL or path to load a new one.'
        )
    prepared = prepare_image(found.data)
    return ImageAttachment(
        number=0,
        data=prepared.data,
        media_type=prepared.media_type,
        width=prepared.width,
        height=prepared.height,
        image_id=identifier,
    )


async def _from_url(url: str) -> ImageAttachment:
    try:
        raw = await download_image(url, max_bytes=MAX_IMAGE_BYTES)
    except DownloadError as exc:
        raise FileToolError(f'The image at {url!r} could not be loaded: {exc}') from exc
    return _prepared(raw, f'the image at {url}')


def _from_path(path: str, root: Path | None = None) -> ImageAttachment:
    target = resolve(path, root=root)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise FileToolError(f'{path!r} could not be read: {exc}') from exc
    return _prepared(raw, path)


def _prepared(raw: bytes, label: str) -> ImageAttachment:
    try:
        return prepare_image(raw)
    except ImageError as exc:
        raise FileToolError(f'{label} is not an image the model can read: {exc}') from exc


async def load_source(messages: Sequence[ModelMessage], source: str, *, root: Path | None = None) -> ImageAttachment:
    """The picture ``source`` names, and the id it will be known by.

    The field takes whatever the model has at hand: an `img_...` id from the history,
    an `http(s)` URL, or a path inside the workspace. The kind is detected from the
    value, so the tool takes one argument and the model does not have to know which
    one applies.

    Raises:
        FileToolError: When the source names nothing readable, or is not an image.
    """
    wanted = source.strip()
    if not wanted:
        raise FileToolError('An image source is required: an id, a URL or a path.')
    if is_url(wanted):
        loaded = await _from_url(wanted)
        return _with_id(loaded, image_id_for(wanted))
    if wanted.startswith(('img_', '[Image #')):
        return _from_session(messages, wanted)
    loaded = _from_path(wanted, root)
    return _with_id(loaded, image_id_for(str(resolve(wanted, root=root))))


def _with_id(attachment: ImageAttachment, identifier: str) -> ImageAttachment:
    """The same picture under the id its source is known by."""
    return ImageAttachment(
        number=attachment.number,
        data=attachment.data,
        media_type=attachment.media_type,
        width=attachment.width,
        height=attachment.height,
        image_id=identifier,
    )


def source_identity(source: str, *, root: Path | None = None) -> str:
    """Identify a source without fetching pixels; still authorize filesystem paths."""
    wanted = source.strip()
    if not wanted:
        raise FileToolError('An image source is required: an id, a URL or a path.')
    if is_url(wanted):
        return image_id_for(wanted)
    if wanted.startswith(('img_', '[Image #')):
        return wanted
    return image_id_for(str(resolve(wanted, root=root)))


def register_image_tools(
    agent: Any,
    logger: ToolLogger | None = None,
    catalog: ToolCatalog | None = None,
    *,
    root: Callable[[], Path] | None = None,
) -> None:
    """Registers the image tool on a pydantic-ai agent."""
    tools = ToolRegistrar(agent, logger, catalog)

    @tools.context_tool(permission=_VIEW, requires='image_input', compaction=VIEW_IMAGE_COMPACTOR)
    async def view_image(ctx: RunContext[Any], *, source: str) -> ToolReturn:
        """Look at an image: one from earlier in this session, or a new one.

        Pictures stop travelling with the conversation once they are a few rounds old;
        what stays is their id, written in the message as `img_...`. Use this when a
        message mentions an image that is no longer attached, or when you need to look
        at a picture the user pointed you at.

        Args:
            source: One of three things, detected automatically — an `img_...` id from
                the conversation, an `http(s)://` URL, or a path to a file inside the
                workspace.
        """
        workspace = None if root is None else root()
        identifier = source_identity(source, root=workspace)
        if identifier in images_in_round(ctx.messages):
            # The loop guard: this round already carries it. Saying so is cheaper than
            # sending the same pixels on every step.
            return ToolReturn(
                return_value=(
                    f'image {identifier} is already attached earlier in this round: '
                    'it is in the conversation above, no need to load it again'
                )
            )

        loaded = await load_source(ctx.messages, source, root=workspace)
        return ToolReturn(
            return_value=(f'image {loaded.image_id} ({loaded.describe()}, ~{loaded.estimated_tokens:,} tokens)'),
            content=[
                BinaryImage(
                    data=loaded.data,
                    media_type=loaded.media_type,  # type: ignore[arg-type]
                    identifier=loaded.image_id,
                )
            ],
        )
