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

from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage

__all__ = ['Recovery']


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
