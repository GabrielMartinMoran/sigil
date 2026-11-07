"""Recovery of a history that a dead process left mid-turn.

A turn is checkpointed after every stream event, which is what makes a long turn
survivable: if the process dies halfway, the session on disk keeps everything the
run completed. The price is that the last checkpoint can land *inside* a tool
batch — a `ModelResponse` that asked for tools and a history that ends there,
because the results never came.

**Only the frontier is repaired, and the frontier is the last response.** That is the
whole rule, and it comes from what pydantic-ai refuses: a new prompt on top of a
response whose calls have no answer raises `Cannot provide a new user prompt when
the message history contains unprocessed tool calls`, and the refusal is permanent —
every message after it fails the same way, so one crash breaks the session for good.
A dangling call in the *middle* is a different animal. The library accepts it (`Agent.run`
only looks at the last response) and closes it in memory before every request
(`_clean_message_history`), so a batch that a steer, a retry or a lost write split in
two is not a turn that died. Repairing those here used to invent tool output that was
never produced, write it to disk — where it became a rollback point of its own — and
then report a crash that never happened.

Two damaged frontiers are closed here, and they need different work:

- **Unanswered tool calls.** `repair_messages` synthesizes a
  `ToolReturnPart(outcome='interrupted')` for each one, marked with
  `pydantic_ai_synthesized_tool_return` so it can be told apart from a real result.
  The library leaves the *last* response alone on purpose — those calls can still be
  continued — so closing them is this module's job.
- **A paused response.** An Anthropic `pause_turn` or an OpenAI background run
  leaves a `state='suspended'` response waiting for a continuation that will never
  arrive, and a new prompt on top of it is refused with its own error. It is
  stamped `interrupted`, which is what it is, and its calls get closed out like
  any other.

The repair is deterministic and idempotent, so running it before every turn costs
one pass over the history and nothing else when the history is healthy — which is
the common case, and the reason a healthy history comes back untouched instead of
normalized: it is the prefix of the model's prompt cache.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolReturnPart,
    repair_messages,
)

SUSPENDED = 'suspended'
"""Lifecycle state of a response a provider paused and expects to continue."""
INTERRUPTED = 'interrupted'
"""Lifecycle state of a response that was cut off, or that the run never acted on."""


@dataclass
class Recovery:
    """The history to use, plus what had to be done to it.

    ``messages`` is what the agent should be given, whether or not anything was
    repaired. ``changed`` says whether it differs from the input, and ``notice`` is
    what the chat says about it — empty when there was nothing to report, so the
    caller can print it unconditionally.
    """

    messages: list[ModelMessage]
    closed_calls: int = 0
    unfinished_responses: int = 0
    changed: bool = False
    notice: str = ''


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


__all__ = ['INTERRUPTED', 'SUSPENDED', 'Recovery', 'recover_history']
