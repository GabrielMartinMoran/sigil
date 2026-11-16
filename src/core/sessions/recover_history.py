"""Recovering a conversation whose turn was cut."""

from __future__ import annotations

from dataclasses import replace

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolReturnPart,
    repair_messages,
)

from src.core.sessions.recovery import Recovery

SUSPENDED = 'suspended'


INTERRUPTED = 'interrupted'


def _frontier_index(messages: list[ModelMessage]) -> int | None:
    """The last response of the history: the only frontier a new prompt trips over."""
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], ModelResponse):
            return index
    return None


def _calls_answered_after(messages: list[ModelMessage], index: int) -> set[str]:
    """The ids that some result after ``index`` answers."""
    answered: set[str] = set()
    for message in messages[index + 1 :]:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id is not None:
                answered.add(part.tool_call_id)
    return answered


def _dangling_on_the_frontier(messages: list[ModelMessage], index: int) -> list[str]:
    """The calls of the frontier response that nothing after it answers.

    A walk and not a set difference: the frontier may repeat a call id by accident, or
    name one that an earlier batch already answered, and either way what matters is
    whether *this* call is answered by what comes after it.
    """
    frontier = messages[index]
    assert isinstance(frontier, ModelResponse)
    answered = _calls_answered_after(messages, index)
    return [call.tool_call_id for call in frontier.tool_calls if call.tool_call_id not in answered]


def _paused_responses(messages: list[ModelMessage]) -> int:
    return sum(1 for message in messages if isinstance(message, ModelResponse) and message.state == SUSPENDED)


def _resume_as_interrupted(messages: list[ModelMessage]) -> list[ModelMessage]:
    """A paused response with no continuation is an interrupted one."""
    return [
        replace(message, state=INTERRUPTED)
        if isinstance(message, ModelResponse) and message.state == SUSPENDED
        else message
        for message in messages
    ]


def _describe(closed: int, paused: int) -> str:
    """One line the user can act on, or an empty string when nothing happened."""
    if not closed and not paused:
        return ''
    pieces: list[str] = []
    if closed:
        calls = f'{closed} tool call' if closed == 1 else f'{closed} tool calls'
        pieces.append(f'closed out {calls} that never got a result')
    if paused:
        pieces.append('discarded a response the provider left paused')
    return (
        f'Recovered the session: {" and ".join(pieces)}. '
        'The previous turn ended before its last tool calls were answered; '
        'this one starts from what it completed.'
    )


def recover_history(messages: list[ModelMessage]) -> Recovery:
    """Makes a history usable again after a process died in the middle of a turn.

    A healthy history is returned as is — the same list, no copy, no merge — so
    nothing a provider caches is ever churned for nothing. That includes a history
    with a dangling call in the middle: it is not this function's to repair.
    """
    if not messages:
        return Recovery(messages=[])

    index = _frontier_index(messages)
    if index is None:
        return Recovery(messages=messages)

    closed = _dangling_on_the_frontier(messages, index)
    paused = _paused_responses(messages)
    if not closed and not paused:
        return Recovery(messages=messages)

    # `repair_messages` closes the dangling calls of the final response, which is
    # exactly the frontier a dead turn leaves behind. Sigil does not defer tool
    # results (its MCP deferral is about tool schemas), so there is no pending work
    # a resumed run could still answer instead.
    return Recovery(
        messages=repair_messages(_resume_as_interrupted(messages)),
        closed_calls=len(closed),
        unfinished_responses=paused,
        changed=True,
        notice=_describe(len(closed), paused),
    )
