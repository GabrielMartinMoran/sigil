"""The ToolErrorRecovery value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import AgentDepsT, RunContext, ToolDefinition

from src.core.agents.describe_tool_error import describe_tool_error


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
