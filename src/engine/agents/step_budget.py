"""What happens when a run uses up the model calls it was given.

Running out of steps is not a crash. It is the most common way a long
investigation ends, and killing the run there throws away the one thing worth
having: what the agent learned before it stopped. So the budget does not abort.
It takes the tools away and asks for a last answer:

```
step 1 .. step N     normal: tools, tool results, more steps
step N + 1           no tools at all, plus a message saying why
                     → the model can only write, so it writes what it has
```

That final message is the subagent's report, or the root's answer, and it is what
lets the parent continue: a subagent that ran out says where it got and what is
left, and the parent can resume it with `summon(id=…)` instead of starting over.

Two details make it deterministic, and both were measured against the real
library rather than assumed:

- ``prepare_tools`` runs **before** ``before_model_request`` for the same request,
  exactly once each. So the count happens in ``prepare_tools`` and the request that
  follows it is the one that knows it is the last.
- The notice is appended to the request's messages directly, not enqueued. An
  enqueued message lands through the pending-drain capability, which is outermost
  and runs at its own time; injecting it here is what guarantees the explanation
  travels *with* the request that has no tools.

The notice also goes into ``ctx.messages``, so it is part of the transcript: the
debug view shows it, the session keeps it, and a resumed run does not repeat it.
"""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, UserPromptPart

if TYPE_CHECKING:
    from pydantic_ai.models import ModelRequestContext
    from pydantic_ai.tools import RunContext, ToolDefinition

PREFIX = 'StepBudget'
"""Prefix of the notice, so it is greppable in a transcript."""


def notice(max_steps: int, *, role: str = 'agent') -> str:
    """What the model reads when its budget is gone.

    It says three things, in this order, because each one is useless without the
    next: the budget is over, nothing else can run, and what to produce instead.
    A model that reads "you are out of steps" alone tends to answer with an
    apology; the shape of the answer has to be asked for explicitly.
    """
    return (
        f'{PREFIX}: you have used all {max_steps} steps allowed for this run, and no more '
        'tools will run. Do not ask for another tool call: it cannot be executed.\n'
        f'Answer now with what you have, as the final message of this {role}. Include:\n'
        '- what you established, with the paths or results that support it;\n'
        '- what you did not get to, and what you would do next;\n'
        '- anything the caller needs to continue without you.'
    )


@dataclass
class StepBudget(AbstractCapability[Any]):
    """Gives a run a fixed number of model calls and one graceful last word."""

    _: KW_ONLY

    max_steps: int
    role: str = 'agent'
    """Word used in the notice, so it reads as "this subagent" or "this turn"."""

    id: str | None = 'step_budget'

    requests: int = 0
    """Model calls seen so far, counted per run."""

    exhausted: bool = False
    """Whether the budget is gone; from here on, no tools are advertised."""

    _announced: bool = False

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError('max_steps must be at least 1; a run with no model call cannot answer')

    @property
    def last_call(self) -> bool:
        """Whether the request being prepared is the extra one."""
        return self.exhausted

    async def prepare_tools(
        self,
        ctx: RunContext[Any],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        self.requests += 1
        self.exhausted = self.requests > self.max_steps
        return [] if self.exhausted else tool_defs

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if not self.exhausted or self._announced:
            return request_context
        self._announced = True
        request = ModelRequest(parts=[UserPromptPart(notice(self.max_steps, role=self.role))])
        # Both, deliberately: the first makes the model read it on this request,
        # the second makes it part of the history, so the transcript explains why
        # the run stopped where it did and a resume does not repeat the warning.
        request_context.messages = [*request_context.messages, request]
        ctx.messages.append(request)
        return request_context


__all__ = ['PREFIX', 'StepBudget', 'notice']
