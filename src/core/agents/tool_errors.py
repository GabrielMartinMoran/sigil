"""Turns tool errors into failed results the model can read.

Without this, a tool exception (a `patch_file` that does not find the
`old_string`, a `read_file` on a nonexistent path) aborts the whole run:
the turn dies, the user takes back control and the agent never learns
what failed and cannot correct itself.

pydantic-ai lets the exception through by default. `ToolFailed` turns it into a
`ToolReturnPart` with `outcome='failed'` that the model sees on the next step.
Unlike `ModelRetry`, it does not consume the tool's retry budget
nor does it ask the model to repeat the same call: it reports the failure so it
can adapt the plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import AgentDepsT, RunContext, ToolDefinition

MAX_ERROR_CHARS = 2000
"""Cap of the message returned to the model, so the context is not inflated."""


def describe_tool_error(error: BaseException) -> str:
    """Message of a failed tool, with the exception type up front."""
    detail = ' '.join(str(error).split()) or '(no detail)'
    if len(detail) > MAX_ERROR_CHARS:
        detail = detail[: MAX_ERROR_CHARS - 1] + '…'
    return f'{type(error).__name__}: {detail}'


@dataclass
class ToolErrorRecovery(AbstractCapability[AgentDepsT]):
    """Returns a tool error to the model instead of bringing the turn down."""

    id: str | None = 'tool_error_recovery'

    async def on_tool_execute_error(
        self,
        ctx: RunContext[AgentDepsT],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        error: Exception,
    ) -> Any:
        raise ToolFailed(describe_tool_error(error)) from error


__all__ = ['MAX_ERROR_CHARS', 'ToolErrorRecovery', 'describe_tool_error']
