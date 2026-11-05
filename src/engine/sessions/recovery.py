"""Recovery of a history that a dead process left mid-turn.

A turn is checkpointed after every stream event, which is what makes a long turn
survivable: if the process dies halfway, the session on disk keeps everything the
run completed. The price is that the last checkpoint can land *inside* a tool
batch — a `ModelResponse` that asked for tools and a history that ends there,
because the results never came.

That tail is fine to write and fatal to continue from. pydantic-ai refuses a new
prompt on top of a response whose calls have no answer — the error is
`Cannot provide a new user prompt when the message history contains unprocessed
tool calls` — and the refusal is permanent: every message after it fails the same
way, so one crash breaks the session for good.

Two damaged tails are closed here, and they need different work:

- **Unanswered tool calls.** `repair_messages` synthesizes a
  `ToolReturnPart(outcome='interrupted')` for each one, marked with
  `pydantic_ai_synthesized_tool_return` so it can be told apart from a real result.
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


def _unanswered_call_ids(messages: list[ModelMessage]) -> list[str]:
    """The calls that no result answers, in the order they were made.

    An ordered walk, the same rule the framework applies: a result answers the
    oldest open call with its id, so a duplicated or out-of-place result cannot
    mask a call that is genuinely dangling.
    """
    open_ids: list[str] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            open_ids.extend(call.tool_call_id for call in message.tool_calls)
        elif isinstance(message, ModelRequest):
            for part in message.parts:
                if not isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    continue
                if part.tool_call_id is not None and part.tool_call_id in open_ids:
                    open_ids.remove(part.tool_call_id)
    return open_ids


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
    return f'Recovered the session: {" and ".join(pieces)}. The previous turn died mid-flight; this one starts from what it completed.'


def recover_history(messages: list[ModelMessage]) -> Recovery:
    """Makes a history usable again after a process died in the middle of a turn.

    A healthy history is returned as is — the same list, no copy, no merge — so
    nothing a provider caches is ever churned for nothing.
    """
    if not messages:
        return Recovery(messages=[])

    closed = _unanswered_call_ids(messages)
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
