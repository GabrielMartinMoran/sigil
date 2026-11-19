"""Dynamic question-tool gate for autonomous goals."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed

from src.core.goal.state import Goal

if TYPE_CHECKING:
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import RunContext, ToolDefinition


@dataclass
class GoalQuestionGate(AbstractCapability[Any]):
    _: KW_ONLY
    current_goal: Callable[[], Goal | None]
    id: str | None = 'goal_question_gate'

    def _disabled(self, tool_name: str) -> bool:
        goal = self.current_goal()
        return bool(goal and goal.yolo and goal.status in ('armed', 'active') and tool_name == 'ask_user')

    async def prepare_tools(self, ctx: RunContext[Any], tool_defs: list[ToolDefinition]) -> list[ToolDefinition]:
        return [tool for tool in tool_defs if not self._disabled(tool.name)]

    async def before_tool_execute(
        self, ctx: RunContext[Any], *, call: ToolCallPart, tool_def: ToolDefinition, args: dict[str, Any]
    ) -> dict[str, Any]:
        if self._disabled(tool_def.name):
            raise ToolFailed(
                'Questions are unavailable in YOLO mode. Decide within existing permissions or report a blocker.'
            )
        return args
