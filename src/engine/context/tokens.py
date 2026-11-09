"""Context token measurement and estimation.

There are two different numbers and they must not be confused:

- **Measured**: the input tokens the provider reported for the last request.
  It is ground truth of what was sent, but it describes the past.
- **Projected**: the measured one plus an estimate of what was added since then
  (new tool results, new prompt). It is what matters to know whether the next
  request fits in the window.

The estimate is ``chars / 4``. It does not claim to be exact: it serves to compare
blocks against each other and to project a delta. The exact number is the measured one.
It underestimates by ~30% against what a provider bills, because a `chars / 4`
heuristic cannot see the per-message envelope (role tokens, separators, the tool
protocol wrapping) the API adds on top of the text. That is why the projection is
anchored on the last measured value and only the delta is estimated: the envelope
of the messages that are already in the measurement is already in the measurement.

**What is measured is only what travels.** The `instructions` recorded on a saved
`ModelRequest` are not part of the history: see `estimate_message_tokens`.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.context.images import image_part_tokens, is_image_part

CHARS_PER_TOKEN = 4
"""Estimation heuristic. The provider's measured value wins when it exists."""

# Same namespace pydantic-ai uses to mark the real context size of
# a response whose usage adds up several requests (long Google decodes,
# Anthropic compactions). See `pydantic_ai._context_size`.
_METADATA_KEY = '__pydantic_ai__'
_CONTEXT_INPUT_TOKENS_KEYS = ('context_input_tokens', 'initial_input_tokens')


def estimate_tokens(text: str) -> int:
    """Token estimate of a text. Empty is zero, not one."""
    if not text:
        return 0
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def _as_text(value: Any) -> str:
    """Converts a part's content to plain text, without breaking."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, dict | list | tuple):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError, ValueError:
            return str(value)
    return str(value)


def part_text(part: Any) -> str:
    """Text a part contributes to the context, to estimate its cost.

    An image contributes no text at all: its cost is in `part_tokens`, which knows
    to price pixels. Stringifying it here is what made a screenshot look like ten
    tokens.
    """
    if isinstance(part, SystemPromptPart | TextPart | ThinkingPart | CompactionPart):
        return part.content or ''
    if isinstance(part, UserPromptPart):
        content = part.content
        if isinstance(content, str):
            return content
        return ' '.join(_as_text(item) for item in content if not is_image_part(item))
    if isinstance(part, ToolCallPart):
        return f'{part.tool_name} {_as_text(part.args)}'
    if isinstance(part, ToolReturnPart | RetryPromptPart):
        return f'{part.tool_name or ""} {tool_return_text(part)}'
    if is_image_part(part):
        return ''
    return _as_text(getattr(part, 'content', ''))


def part_tokens(part: Any) -> int:
    """Cost estimate of one part, images included.

    It is the single place that knows a part may not be text. Every caller that
    priced a message by summing `part_text` was undercounting every image in the
    history by four orders of magnitude.

    A multimodal payload can arrive in a user prompt or in a tool result: `view_image`
    hands a picture back as part of its answer, and that image costs what any other
    image costs.
    """
    if is_image_part(part):
        return image_part_tokens(part)
    if isinstance(part, (UserPromptPart, ToolReturnPart)) and isinstance(part.content, list):
        # A multimodal payload is text plus files: each item is priced by its own
        # kind, so a prompt with a picture is not charged as its caption.
        return estimate_tokens(part_text(part)) + sum(
            image_part_tokens(item) for item in part.content if is_image_part(item)
        )
    return estimate_tokens(part_text(part))


def tool_return_text(part: Any) -> str:
    """Text the model sees from a tool result.

    Sigil tools return pydantic models (`FileContent`, `PatchResult`,
    `LoadedSkill`), not strings. `model_response_str()` is the exact serialization
    that travels to the provider; `str(content)` would give the Python repr and lie
    about the size.
    """
    model_response_str = getattr(part, 'model_response_str', None)
    if callable(model_response_str):
        # Estimation must never break the request.
        with contextlib.suppress(Exception):
            text = model_response_str()
            if isinstance(text, str):
                return text
    return _as_text(getattr(part, 'content', ''))


def estimate_message_tokens(message: ModelMessage) -> int:
    """Cost estimate of a whole message: its parts only.

    The `instructions` a saved `ModelRequest` carries are **not** counted. They
    are not resent with the history: pydantic-ai sends the instructions of the
    request being made (`model_request_parameters.instruction_parts`), and the
    ones recorded on earlier messages only document what that request was made
    with. Counting them made a conversation weigh what its instructions weigh
    once per message —a 12x overestimate on a measured session— and the surplus
    pushed the history over the budget, so whole turns were discarded.

    What the instructions cost on this request is measured once, from the
    parameters (`src.engine.context.overhead`), and travels in `ContextBudget`.
    """
    return sum(part_tokens(part) for part in getattr(message, 'parts', ()))


def estimate_messages_tokens(messages: Sequence[ModelMessage]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def _marked_context_input_tokens(response: ModelResponse) -> int | None:
    """Input tokens that measure the context, when the usage adds up several requests."""
    namespace = (response.metadata or {}).get(_METADATA_KEY)
    if not isinstance(namespace, dict):
        return None
    for key in _CONTEXT_INPUT_TOKENS_KEYS:
        value = namespace.get(key)
        if isinstance(value, int):
            return value
    return None


def response_context_tokens(response: ModelResponse) -> int:
    """Context a response leaves behind: input (measured) plus generated output.

    It mirrors `pydantic_ai._context_size.context_tokens`, which is private. If it changes
    semantics, the measured value degrades to ``usage.input_tokens + output_tokens``,
    which is still a reasonable bound.
    """
    marked = _marked_context_input_tokens(response)
    input_tokens = response.usage.input_tokens if marked is None else marked
    return (input_tokens or 0) + (response.usage.output_tokens or 0)


def last_response_index(messages: Sequence[ModelMessage]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], ModelResponse):
            return index
    return None


def measured_tokens(messages: Sequence[ModelMessage]) -> int | None:
    """Tokens of the last request the provider reported, or ``None`` if there is none."""
    index = last_response_index(messages)
    if index is None:
        return None
    response = messages[index]
    assert isinstance(response, ModelResponse)
    if response.usage.input_tokens is None and response.usage.output_tokens is None:
        return None
    return response_context_tokens(response)


def projected_tokens(messages: Sequence[ModelMessage]) -> int | None:
    """Estimate of the next request: measured plus what was added since the last response.

    If there is no response yet (first request of the turn), it projects the whole
    history by estimation.
    """
    index = last_response_index(messages)
    if index is None:
        return estimate_messages_tokens(messages) or None
    measured = measured_tokens(messages)
    if measured is None:
        return estimate_messages_tokens(messages) or None
    return measured + estimate_messages_tokens(messages[index + 1 :])
