"""YOLO hides interactive questions without relaxing other tool restrictions."""

import pytest
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.tools import ToolDefinition

from src.core.goal.policy import GoalQuestionGate
from src.core.goal.state import Goal


@pytest.mark.anyio
async def test_yolo_hides_and_refuses_ask_user_even_after_tool_preparation() -> None:
    goal = Goal('ship', yolo=True)
    gate = GoalQuestionGate(current_goal=lambda: goal)
    ask = ToolDefinition(name='ask_user', parameters_json_schema={'type': 'object'})
    read = ToolDefinition(name='read_file', parameters_json_schema={'type': 'object'})

    assert await gate.prepare_tools(None, [ask, read]) == [read]
    with pytest.raises(ToolFailed):
        await gate.before_tool_execute(None, call=None, tool_def=ask, args={})
    assert await gate.before_tool_execute(None, call=None, tool_def=read, args={}) == {}


@pytest.mark.anyio
async def test_stopped_or_normal_goal_leaves_question_available() -> None:
    goal = Goal('ship', yolo=True, status='stopped')
    gate = GoalQuestionGate(current_goal=lambda: goal)
    ask = ToolDefinition(name='ask_user', parameters_json_schema={'type': 'object'})
    assert await gate.prepare_tools(None, [ask]) == [ask]
    assert await gate.before_tool_execute(None, call=None, tool_def=ask, args={}) == {}
