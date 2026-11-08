"""Levers that rewrite the history: reasoning, tool results and turns."""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.agents.turn_map import library_run_id
from src.engine.context.images import is_image_part
from src.engine.context.images_cache import ImageVerdict, decide
from src.engine.context.strategy.turns import has_pinned_result, split_prompts
from src.engine.context.tokens import estimate_messages_tokens, estimate_tokens, tool_return_text


def drop_thinking(messages: list[ModelMessage]) -> tuple[list[ModelMessage], int]:
    """Strips `ThinkingPart` from the assistant responses."""
    result: list[ModelMessage] = []
    dropped = 0
    for message in messages:
        if not isinstance(message, ModelResponse):
            result.append(message)
            continue
        kept = [part for part in message.parts if not isinstance(part, ThinkingPart)]
        dropped += len(message.parts) - len(kept)
        result.append(message if len(kept) == len(message.parts) else dataclasses.replace(message, parts=kept))
    return result, dropped


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


def thinking_labels(messages: Sequence[ModelMessage]) -> list[str | None]:
    """The turn each step with reasoning belonged to, in order.

    A turn is pydantic-ai's run: the library stamps the same identifier on every
    message of one, and a prompt typed while the agent works is enqueued into the
    same one, so steering does not open a second turn. The identifier itself is named
    in exactly one module, `agents/turn_map.py`; here the domain word is enough.
    """
    return [
        library_run_id(message)
        for message in messages
        if isinstance(message, ModelResponse) and any(isinstance(part, ThinkingPart) for part in message.parts)
    ]


def steps_with_reasoning(messages: Sequence[ModelMessage], *, provider: str | None = None) -> int:
    """How many responses contain compatible reasoning across the execution."""
    return sum(
        isinstance(message, ModelResponse)
        and any(isinstance(part, ThinkingPart) and accepts_replayed_reasoning(part, provider) for part in message.parts)
        for message in messages
    )


def keep_fitting_steps(
    messages: list[ModelMessage],
    available: int,
    *,
    provider: str | None = None,
) -> list[ModelMessage] | None:
    """Rewrites the history keeping as many reasoning steps as fit in `available`.

    It starts from the most the execution can keep and shrinks one step at a
    time, so the result is the largest window that fits. Returns ``None`` when not
    even dropping all the reasoning makes it fit: the caller then has to truncate
    tool results or discard turns, which costs more than reasoning does.
    """
    if estimate_messages_tokens(messages) <= available:
        return messages
    for steps in range(steps_with_reasoning(messages, provider=provider) - 1, -1, -1):
        shrunk, _, _ = keep_recent_steps(messages, steps=steps, provider=provider)
        if estimate_messages_tokens(shrunk) <= available:
            return shrunk
    return None


def keep_recent_steps(
    messages: list[ModelMessage],
    *,
    steps: int,
    provider: str | None = None,
    max_tokens: int | None = None,
) -> tuple[list[ModelMessage], int, int]:
    """Keep the newest compatible reasoning steps across turns, within a token cap.

    This is the budget lever, not the operational retention policy. The coordinator
    controls when a frontier advances; a view always starts from the original log.
    """
    if steps <= 0 or max_tokens == 0:
        result, dropped = drop_thinking(messages)
        return result, 0, dropped

    # Budget fitting can retain reasoning across user turns. Operational hysteresis
    # is handled by the coordinator; this helper only selects the newest steps.
    keep: set[int] = set()
    kept = 0
    spent = 0
    for order in range(len(messages) - 1, -1, -1):
        if kept >= steps:
            break
        message = messages[order]
        if not isinstance(message, ModelResponse):
            continue
        parts = [part for part in message.parts if isinstance(part, ThinkingPart)]
        if not parts:
            continue
        if not all(accepts_replayed_reasoning(part, provider) for part in parts):
            continue
        cost = sum(estimate_tokens(part.content or '') for part in parts)
        if max_tokens is not None and spent + cost > max_tokens:
            # A block that does not fit is not kept, and the ones further back cost
            # the same or more: the budget is spent.
            break
        keep.add(order)
        kept += 1
        spent += cost

    result: list[ModelMessage] = []
    dropped = 0
    for order, message in enumerate(messages):
        if not isinstance(message, ModelResponse):
            result.append(message)
            continue
        if order in keep:
            result.append(message)
            continue
        kept_parts = [part for part in message.parts if not isinstance(part, ThinkingPart)]
        dropped += len(message.parts) - len(kept_parts)
        result.append(
            message if len(kept_parts) == len(message.parts) else dataclasses.replace(message, parts=kept_parts)
        )
    return result, kept, dropped


def _truncate_text(text: str, head_chars: int) -> str:
    tokens = estimate_tokens(text)
    kept = min(len(text), head_chars)
    while True:
        omitted = len(text) - kept
        notice = f'\n\n[tool result truncated: {omitted:,} chars omitted, ~{tokens:,} tokens total]'
        adjusted = max(0, min(kept, head_chars - len(notice)))
        if adjusted == kept:
            return (text[:kept] + notice)[:head_chars]
        kept = adjusted


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
    verdicts = decide(messages, turn_window=turn_window, max_tokens=max_tokens)
    if not verdicts:
        return messages, 0

    result: list[ModelMessage] = []
    dropped = 0
    for message_index, message in enumerate(messages):
        if not isinstance(message, ModelRequest):
            result.append(message)
            continue
        parts: list[object] = []
        count = 0
        for part in message.parts:
            if not (isinstance(part, (UserPromptPart, ToolReturnPart)) and isinstance(part.content, list)):
                parts.append(part)
                continue
            kept: list[object] = []
            notes: list[str] = []
            for item_index, item in enumerate(part.content):
                verdict = verdicts.get((message_index, item_index)) if is_image_part(item) else None
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


def count_images(messages: Sequence[ModelMessage]) -> int:
    """How many image parts a group of messages carries."""
    return sum(
        1
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart) and isinstance(part.content, list)
        for item in part.content
        if is_image_part(item)
    )


def truncate_old_tool_results(
    turns: list[list[ModelMessage]],
    *,
    recent_start: int,
    max_chars: int,
    head_chars: int,
    pinned_tools: frozenset[str] = frozenset(),
) -> tuple[list[ModelMessage], int]:
    """Truncates the tool results of the old turns, leaving the recent ones intact.

    It measures the text the model actually sees (`model_response_str()`), not the
    object's `str()`: Sigil tools return pydantic models, and comparing
    against `isinstance(content, str)` left the truncation without effect.
    """
    result: list[ModelMessage] = []
    truncated = 0
    for index, turn in enumerate(turns):
        if index >= recent_start:
            result.extend(turn)
            continue
        for message in turn:
            if not isinstance(message, ModelRequest):
                result.append(message)
                continue
            parts = []
            changed = False
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name not in pinned_tools:
                    text = tool_return_text(part)
                    if len(text) > max_chars:
                        parts.append(
                            dataclasses.replace(part, content=_truncate_text(text, min(head_chars, max_chars)))
                        )
                        truncated += 1
                        changed = True
                        continue
                parts.append(part)
            result.append(dataclasses.replace(message, parts=parts) if changed else message)
    return result, truncated


def fit_to_budget(
    messages: list[ModelMessage],
    available: int,
    *,
    pinned_tools: frozenset[str] = frozenset(),
    keep_head: Sequence[ModelMessage] = (),
    keep_recent: int = 1,
) -> tuple[list[ModelMessage], int]:
    """Discards whole turns until it fits in the budget.

    It prefers discarding turns without pinned results: if you have to choose between
    losing an old `read_file` and losing a skill's instructions, the
    `read_file` is what gets lost.

    ``keep_head`` are the messages that are never discarded (the compaction
    summary). ``keep_recent`` is how many turns at the end are protected: the
    caller decides the floor, and a floor of one means the turn in progress always
    survives.
    """
    head = list(keep_head)
    turns = split_prompts(list(messages))
    dropped = 0
    floor = max(1, keep_recent)

    def total() -> int:
        return estimate_messages_tokens([*head, *(message for turn in turns for message in turn)])

    if estimate_messages_tokens([*head, *messages]) <= available:
        return [*head, *messages], 0

    while len(turns) > floor and total() > available:
        # Only the turns outside the protected tail are candidates.
        candidates = range(len(turns) - floor)
        victim = next(
            (index for index in candidates if not has_pinned_result(turns[index], pinned_tools)),
            None,
        )
        if victim is None:
            # Everything discardable carries instructions: the oldest one goes anyway.
            victim = 0
        dropped += len(turns[victim])
        turns.pop(victim)

    return [*head, *(message for turn in turns for message in turn)], dropped
