"""What the view is allowed to rewrite: image attachments and foreign reasoning."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ThinkingPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.context.compaction.identity import part_key
from src.engine.context.images import is_image_part
from src.engine.context.images_cache import ImageVerdict, decide


def accepts_replayed_reasoning(part: ThinkingPart, provider: str | None) -> bool:
    """Whether the receiving provider can be sent this reasoning back.

    "Signatures are only sent back to the same provider" (`ThinkingPart.signature`):
    Anthropic, Bedrock, Google and OpenAI sign the reasoning and accept their own
    signature back, not another's. Keeping a signed block the receiver is going to
    reject spends history on something the adapter renders as plain text anyway.

    Unsigned reasoning has no owner, so it travels to any provider. A part that
    names no provider, or an unknown receiver, is trusted: the adapter decides, and
    a wrong guess costs a retry, not a rejected request.
    """
    if not part.signature:
        return True
    if part.provider_name is None or provider is None:
        return True
    return part.provider_name == provider


IMAGE_OMITTED = '[image {image_id} from an earlier turn: not attached here, use view_image to look at it]'
"""What replaces an image the view drops.

The id is what makes the picture recoverable: `view_image` takes it and hands the
bytes back, so dropping the pixels costs a tool call instead of the information.

A session saved before ids existed still carries `[Image #n]` as the identifier,
which repeats across turns; the note says so honestly rather than pretending it is
a handle that will resolve.
"""

IMAGE_OVER_BUDGET = '[image {image_id} left out to stay inside the image budget: use view_image to look at it]'
"""What replaces a picture the pixel cap could not afford."""

IMAGE_SUPERSEDED = '[image {image_id} also appears attached later in this conversation: not repeated here]'
"""What replaces a copy of an image that is already travelling.

`view_image` hands back a picture the prompt may still be carrying. Sending both
copies charges the same pixels twice and tells the model it is looking at two
different pictures.
"""


def drop_stale_images(
    messages: list[ModelMessage],
    *,
    turn_window: int,
    max_tokens: int | None = None,
) -> tuple[list[ModelMessage], int]:
    """Leaves a note in place of every image that must not travel.

    The decision is `images_cache.decide`: an image is loaded in the turn it arrived
    in and stays for a few turns, the newest copy of a picture is the one that
    counts, and the pixels have a ceiling. This function only applies the verdicts and
    writes the note, so the model knows the picture existed and what id brings it back.

    Args:
        messages: The history, flat.
        turn_window: Turns an image stays loaded, counting its own.
        max_tokens: Ceiling for the pixels that travel, in tokens.

    Returns:
        The rewritten messages and how many images were dropped.
    """
    removals = image_removals(messages, turn_window=turn_window, max_tokens=max_tokens)
    return apply_image_removals(messages, removals)


def image_removals(messages: Sequence[ModelMessage], *, turn_window: int, max_tokens: int | None) -> dict[str, str]:
    """Decide once at a boundary; persist keys rather than transient message positions."""
    verdicts = decide(messages, turn_window=turn_window, max_tokens=max_tokens)
    return {
        f'{part_key(messages[mi], messages[mi].parts[pi], pi)}:image:{ii}': str(verdict)
        for (mi, pi, ii), verdict in verdicts.items()
        if not verdict.travels
    }


def apply_image_removals(
    messages: Sequence[ModelMessage], removals: Mapping[str, str]
) -> tuple[list[ModelMessage], int]:
    """Replay the same image notes even when the current request is not under pressure."""

    result: list[ModelMessage] = []
    dropped = 0
    for message in messages:
        if not isinstance(message, ModelRequest):
            result.append(message)
            continue
        parts: list[object] = []
        count = 0
        for part_index, part in enumerate(message.parts):
            if not (isinstance(part, (UserPromptPart, ToolReturnPart)) and isinstance(part.content, list)):
                parts.append(part)
                continue
            kept: list[object] = []
            notes: list[str] = []
            for item_index, item in enumerate(part.content):
                key = f'{part_key(message, part, part_index)}:image:{item_index}'
                reason = removals.get(key) if is_image_part(item) else None
                verdict = ImageVerdict(reason) if reason else None
                if verdict is None or verdict.travels:
                    kept.append(item)
                    continue
                count += 1
                notes.append(_note_for(verdict, _identifier_of(item)))
            if not notes:
                parts.append(part)
                continue
            if isinstance(part, ToolReturnPart):
                # The result keeps its own text; the note goes beside it, not inside
                # the list the provider reads as the tool's payload.
                parts.append(dataclasses.replace(part, content=[*kept, *notes]))
            else:
                parts.append(dataclasses.replace(part, content=[*kept, '\n', *notes]))
        dropped += count
        result.append(dataclasses.replace(message, parts=parts) if count else message)
    return result, dropped


def _note_for(verdict: ImageVerdict, identifier: str) -> str:
    """What the model reads in place of the pixels."""
    if verdict is ImageVerdict.SUPERSEDED:
        return IMAGE_SUPERSEDED.format(image_id=identifier)
    if verdict is ImageVerdict.OVER_BUDGET:
        return IMAGE_OVER_BUDGET.format(image_id=identifier)
    return IMAGE_OMITTED.format(image_id=identifier)


def _identifier_of(item: object) -> str:
    """The handle `view_image` will accept for this image part.

    An image built by the composer carries an `img_...` id. One that came from
    anywhere else —an MCP resource, a legacy session— carries whatever identifier
    pydantic-ai derived from its bytes, which is still a stable handle: `view_image`
    looks the history up by identifier, not by the shape of the string.
    """
    identifier = getattr(item, 'identifier', None)
    if isinstance(identifier, str) and identifier:
        return identifier
    return '(no id)'
