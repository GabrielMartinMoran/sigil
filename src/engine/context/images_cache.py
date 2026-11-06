"""Which images travel with the request, and why the rest do not.

An image costs what its pixels cost, and it costs it on every request while it
travels. The rule that keeps that bounded is one line: **an image is loaded in the
turn it arrived in, and it stays loaded for a while after that**.

The turn is the identifier pydantic-ai stamps on one run, the same unit the
reasoning window uses: everything until
control goes back to the user, with steering in the same turn. So an image loaded
by a prompt, by `view_image` or by anything else is stamped by the message that
carries it — there is no second store and nothing to keep in sync.

Three reasons to unload one, and each leaves a note with the id:

1. **A newer copy is attached.** The same picture pasted and then fetched again
   would otherwise be paid twice and read as two different pictures.
2. **Its turn is older than the window.** A picture matters while it is being
   discussed; after a few turns it is a record, not working memory.
3. **The pixels are past the cap.** Three screenshots of a dense table can cost more
   than the whole conversation around them.

Everything unloaded stays in the session log, so `view_image` recovers it by id. The
note is what makes that discoverable; without it the model would not know a picture
was ever there.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart, UserPromptPart

from src.engine.agents.turn_map import library_run_id
from src.engine.context.images import image_part_tokens, is_image_part


class ImageVerdict(StrEnum):
    """What happens to one image occurrence in the view."""

    KEEP = 'keep'
    SUPERSEDED = 'superseded'
    """A newer copy of the same picture is attached further on."""
    EXPIRED = 'expired'
    """Its turn is older than the window."""
    OVER_BUDGET = 'over_budget'
    """The pixel cap was spent before reaching it."""

    @property
    def travels(self) -> bool:
        return self is ImageVerdict.KEEP


@dataclass(frozen=True, slots=True)
class ImageOccurrence:
    """One image part, where it lives and when it was loaded."""

    identifier: str
    turn: str | None
    turn_index: int
    tokens: int
    message_index: int
    item_index: int

    @property
    def position(self) -> tuple[int, int]:
        return (self.message_index, self.item_index)


def occurrences(messages: Sequence[ModelMessage]) -> list[ImageOccurrence]:
    """Every image part in the history, oldest first, with its turn resolved.

    A message with no turn of its own —one saved before the library stamped an
    identifier— counts as its own, so it ages like anything else instead of living
    forever.
    """
    return _scan(messages)[0]


def current_turn(messages: Sequence[ModelMessage]) -> int:
    """Index of the turn the latest message belongs to.

    It is read from the whole history, not from the images: an old picture must be
    able to expire even when the last few turns carried none.
    """
    return _scan(messages)[1]


def _scan(messages: Sequence[ModelMessage]) -> tuple[list[ImageOccurrence], int]:
    """Images and the last turn index, in one pass over the history.

    Three counters, three names, because they are three things: `turn_keys` is the
    turn a message belongs to, `prompt_index` counts how many user prompts have been
    seen (the fallback for a turn with no identifier), and `turn_index` is the
    position of the current turn among the turns of the history.
    """
    found: list[ImageOccurrence] = []
    turn_keys: list[str] = []
    prompt_index = -1
    turn_index = 0
    for message_index, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            continue
        starts_prompt = any(isinstance(part, UserPromptPart) for part in message.parts)
        if starts_prompt or not turn_keys:
            prompt_index += 1
        key = library_run_id(message) or f'prompt-{prompt_index}'
        if key not in turn_keys:
            turn_keys.append(key)
        turn_index = turn_keys.index(key)
        for part in message.parts:
            if not isinstance(part, (UserPromptPart, ToolReturnPart)):
                continue
            if not isinstance(part.content, list):
                continue
            for item_index, item in enumerate(part.content):
                if not is_image_part(item):
                    continue
                found.append(
                    ImageOccurrence(
                        identifier=_identifier_of(item),
                        turn=library_run_id(message),
                        turn_index=turn_index,
                        tokens=image_part_tokens(item),
                        message_index=message_index,
                        item_index=item_index,
                    )
                )
    return found, turn_index


def _identifier_of(item: object) -> str:
    """The handle the model was given for this image, or a placeholder."""
    identifier = getattr(item, 'identifier', None)
    return identifier if isinstance(identifier, str) and identifier else '(no id)'


def decide(
    messages: Sequence[ModelMessage],
    *,
    turn_window: int,
    max_tokens: int | None,
) -> dict[tuple[int, int], ImageVerdict]:
    """The verdict for every image occurrence, keyed by its position.

    Args:
        messages: The history.
        turn_window: Turns an image stays loaded, counting its own. ``1`` keeps
            only the turn in progress.
        max_tokens: Ceiling for the pixels that travel, in tokens. ``None`` leaves the
            turn window as the only limit.
    """
    found, last_turn = _scan(messages)
    if not found:
        return {}

    verdicts: dict[tuple[int, int], ImageVerdict] = {}

    # The last occurrence of an id is the live one: fetching a picture again is what
    # moves it forward, and that is the copy the conversation is about.
    live: dict[str, ImageOccurrence] = {}
    for occurrence in found:
        live[occurrence.identifier] = occurrence

    spent = 0
    for occurrence in live.values():
        age = last_turn - occurrence.turn_index
        if age >= max(1, turn_window):
            verdicts[occurrence.position] = ImageVerdict.EXPIRED
        elif max_tokens is not None and spent + occurrence.tokens > max_tokens:
            verdicts[occurrence.position] = ImageVerdict.OVER_BUDGET
        else:
            verdicts[occurrence.position] = ImageVerdict.KEEP
            spent += occurrence.tokens

    for occurrence in found:
        if occurrence.position in verdicts:
            continue
        # Not the live copy: the identifier's own verdict is what applies to it.
        verdicts[occurrence.position] = ImageVerdict.SUPERSEDED
    return verdicts
