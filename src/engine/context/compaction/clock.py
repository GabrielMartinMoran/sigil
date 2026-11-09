"""The clock a compaction event is measured in: steps of one execution.

A step is a model response that became part of the transcript. Parallel tool calls of
the same response are one step, because they were one decision; a steer typed while the
agent works belongs to the turn it reached, because that is the turn the library stamped
it with.

The clock is derived from the transcript and never stored. That is what makes it correct
after a resume (the messages come back, so the count comes back), after a rollback (the
messages left, so the count left) and in a fork (a new transcript, its own count).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelResponse


def step_of_message(messages: Sequence[ModelMessage]) -> list[int]:
    """The step each message belongs to, index-aligned with ``messages``.

    Messages before the first response belong to step ``0``: the prompt of a turn that
    has not answered yet is not a step, it is what the next step answers.
    """
    steps: list[int] = []
    current = 0
    for message in messages:
        if isinstance(message, ModelResponse):
            current += 1
        steps.append(current)
    return steps


def step_count(messages: Sequence[ModelMessage]) -> int:
    """How many steps the execution reached."""
    return sum(1 for message in messages if isinstance(message, ModelResponse))


def tool_call_steps(messages: Sequence[ModelMessage]) -> dict[str, int]:
    """The step each tool call belongs to, by ``tool_call_id``.

    Tool returns are not consulted: the call carries the id and is what the age is
    measured from.
    """
    steps = step_of_message(messages)
    ages: dict[str, int] = {}
    for index, message in enumerate(messages):
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            call_id = getattr(part, 'tool_call_id', None)
            if call_id:
                ages.setdefault(str(call_id), steps[index])
    return ages


__all__ = ['step_count', 'step_of_message', 'tool_call_steps']
