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

from src.engine.context.images import image_part_tokens, is_image_part
from src.engine.context.strategy.turns import has_pinned_result, split_turns
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
    """The round each step with reasoning belonged to, in order.

    `run_id` is the round: pydantic-ai stamps the same id on every message of one
    run, and a prompt typed while the agent works is enqueued into the same run, so
    steering does not open a new round. Sessions saved before `run_id` existed group
    per message instead of merging unrelated rounds into one.
    """
    return [
        message.run_id
        for message in messages
        if isinstance(message, ModelResponse) and any(isinstance(part, ThinkingPart) for part in message.parts)
    ]


def steps_with_reasoning(messages: Sequence[ModelMessage], *, provider: str | None = None) -> int:
    """How many reasoning blocks of the round in progress can be kept.

    It is the ceiling of the window: asking for more than this would keep the
    previous rounds' reasoning too, which is a record, not working memory.
    """
    rounds = thinking_labels(messages)
    current_round = rounds[-1] if rounds else None
    return sum(
        1
        for message in messages
        if isinstance(message, ModelResponse)
        and message.run_id == current_round
        and any(isinstance(part, ThinkingPart) for part in message.parts)
        and all(accepts_replayed_reasoning(part, provider) for part in message.parts if isinstance(part, ThinkingPart))
    )


def keep_fitting_steps(
    messages: list[ModelMessage],
    available: int,
    *,
    provider: str | None = None,
) -> list[ModelMessage] | None:
    """Rewrites the history keeping as many reasoning steps as fit in `available`.

    It starts from the most the current round can keep and shrinks one step at a
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
    """Keeps the reasoning of the last `steps` steps of the round in progress.

    The round in progress is the one the latest step belongs to. Its steps are
    walked backwards until either `steps` reasoning blocks are kept or the round
    ends, so a short round does not borrow the previous one's reasoning. Every other
    reasoning block is dropped: it is not working memory, it is a record, and it
    costs what it costs on every request.

    The window is on steps, not on the round, because resending a whole round is
    quadratic: a measured 317-step round would bill 44M extra tokens, while the loop
    this fixes is local to a few steps.

    Args:
        messages: The history.
        steps: How many steps keep their reasoning.
        provider: The provider the request goes to, to filter signed blocks.
        max_tokens: Ceiling for the reasoning kept, in tokens. ``None`` means the
            step count is the only limit, which is wrong when the context is nearly
            full: a wide window would starve the turn it is meant to help.
    """
    if steps <= 0 or max_tokens == 0:
        result, dropped = drop_thinking(messages)
        return result, 0, dropped

    rounds = thinking_labels(messages)
    current_round = rounds[-1] if rounds else None

    # Walk the round in progress backwards: the steps closest to the work in
    # progress are the ones that stop the agent from re-deciding what it already
    # decided. A round shorter than the window keeps only its own reasoning.
    keep: set[int] = set()
    kept = 0
    spent = 0
    for order in range(len(messages) - 1, -1, -1):
        if kept >= steps:
            break
        message = messages[order]
        if not isinstance(message, ModelResponse) or message.run_id != current_round:
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
    omitted = len(text) - head_chars
    tokens = estimate_tokens(text)
    return f'{text[:head_chars]}\n\n[tool result truncated: {omitted:,} chars omitted, ~{tokens:,} tokens total]'


IMAGE_OMITTED = '[image {image_id} from an earlier turn: not attached here, use view_image to look at it]'
"""What replaces an image the view drops.

The id is what makes the picture recoverable: `view_image` takes it and hands the
bytes back, so dropping the pixels costs a tool call instead of the information.

A session saved before ids existed still carries `[Image #n]` as the identifier,
which repeats across turns; the note says so honestly rather than pretending it is
a handle that will resolve.
"""


def drop_old_images(
    turns: list[list[ModelMessage]],
    *,
    recent_start: int,
    max_tokens: int | None = None,
) -> tuple[list[ModelMessage], int]:
    """Drops the image parts of the old turns, keeping their text and their ids.

    An image is the most expensive item in the history per unit of usefulness: it
    matters while the conversation is about it and costs full price forever. The
    text is kept and a note says the image was there and under what id, so the model
    still knows the conversation happened while the pixels stop being paid for on
    every step.

    Args:
        turns: The conversation, split into turns.
        recent_start: Index of the first turn kept whole.
        max_tokens: Ceiling for the pixels that stay, in tokens. Images past it are
            dropped too, oldest first, even inside the turns that were kept whole:
            it is the same idea as the cap on reasoning, for the other item that is
            expensive by nature. ``None`` keeps every image of the recent turns.

    Returns:
        The flattened messages and how many images were dropped.
    """
    kept_tokens = 0
    result: list[ModelMessage] = []
    dropped = 0
    for index, turn in enumerate(turns):
        for message in turn:
            if index < recent_start:
                message, count = _drop_images_of(message)
                dropped += count
            elif max_tokens is not None:
                message, count, kept_tokens = _drop_images_past_budget(message, max_tokens, kept_tokens)
                dropped += count
            result.append(message)
    return result, dropped


def _drop_images_past_budget(message: ModelMessage, max_tokens: int, kept_tokens: int) -> tuple[ModelMessage, int, int]:
    """Keeps the images that still fit, dropping the ones past the ceiling."""
    if not isinstance(message, ModelRequest):
        return message, 0, kept_tokens
    parts: list[object] = []
    dropped = 0
    for part in message.parts:
        if not (isinstance(part, (UserPromptPart, ToolReturnPart)) and isinstance(part.content, list)):
            parts.append(part)
            continue
        kept: list[object] = []
        notes: list[str] = []
        for item in part.content:
            if not is_image_part(item):
                kept.append(item)
                continue
            cost = image_part_tokens(item)
            if kept_tokens + cost <= max_tokens:
                kept.append(item)
                kept_tokens += cost
                continue
            dropped += 1
            notes.append(IMAGE_OMITTED.format(image_id=_identifier_of(item)))
        if not notes:
            parts.append(part)
            continue
        if isinstance(part, ToolReturnPart):
            parts.append(dataclasses.replace(part, content=[*kept, *notes]))
        else:
            parts.append(dataclasses.replace(part, content=[*kept, '\n', *notes]))
    if not dropped:
        return message, 0, kept_tokens
    return dataclasses.replace(message, parts=parts), dropped, kept_tokens


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


def _drop_images_of(message: ModelMessage) -> tuple[ModelMessage, int]:
    """Removes the image parts of one request, leaving a note per image.

    Two carriers are covered: a user prompt (`look at [Image #1]`) and a tool
    result (`view_image` hands a picture back). The second one matters as much as
    the first: an image a tool returned sits in the history at full price forever
    unless something drops it.

    One note per image, not one per group: each carries the id that brings that
    picture back, and a single combined line would lose all but one of them.
    """
    if not isinstance(message, ModelRequest):
        return message, 0
    parts: list[object] = []
    dropped = 0
    for part in message.parts:
        if not (isinstance(part, (UserPromptPart, ToolReturnPart)) and isinstance(part.content, list)):
            parts.append(part)
            continue
        kept: list[object] = []
        notes: list[str] = []
        for item in part.content:
            if is_image_part(item):
                notes.append(IMAGE_OMITTED.format(image_id=_identifier_of(item)))
                dropped += 1
            else:
                kept.append(item)
        if not notes:
            parts.append(part)
            continue
        if isinstance(part, ToolReturnPart):
            # The result keeps its own text; the note goes beside it, not inside the
            # list the provider reads as the tool's payload.
            parts.append(dataclasses.replace(part, content=[*kept, *notes]))
        else:
            parts.append(dataclasses.replace(part, content=[*kept, '\n', *notes]))
    if not dropped:
        return message, 0
    return dataclasses.replace(message, parts=parts), dropped


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
                        parts.append(dataclasses.replace(part, content=_truncate_text(text, head_chars)))
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
) -> tuple[list[ModelMessage], int]:
    """Discards whole turns until it fits in the budget.

    It prefers discarding turns without pinned results: if you have to choose between
    losing an old `read_file` and losing a skill's instructions, the
    `read_file` is what gets lost.

    ``keep_head`` are the messages that are never discarded (the compaction
    summary).
    """
    head = list(keep_head)
    turns = split_turns(list(messages))
    dropped = 0

    def total() -> int:
        return estimate_messages_tokens([*head, *(message for turn in turns for message in turn)])

    if estimate_messages_tokens([*head, *messages]) <= available:
        return [*head, *messages], 0

    while len(turns) > 1 and total() > available:
        victim = next(
            (index for index, turn in enumerate(turns[:-1]) if not has_pinned_result(turn, pinned_tools)),
            None,
        )
        if victim is None:
            # Everything discardable carries instructions: the oldest one goes anyway.
            victim = 0
        dropped += len(turns[victim])
        turns.pop(victim)

    return [*head, *(message for turn in turns for message in turn)], dropped
