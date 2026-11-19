"""Session goal state and command parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GoalStatus = Literal['armed', 'active', 'waiting', 'blocked', 'completed', 'stopped']
GoalAction = Literal['set', 'stop', 'status']


@dataclass(frozen=True, slots=True)
class Goal:
    text: str
    yolo: bool = False
    status: GoalStatus = 'armed'
    revision: int = 1
    continuations: int = 0

    def replace(self, text: str, *, yolo: bool = False) -> Goal:
        return Goal(text=text, yolo=yolo, revision=self.revision + 1)


@dataclass(frozen=True, slots=True)
class GoalCommand:
    action: GoalAction
    text: str = ''
    yolo: bool = False


def parse_goal_command(argument: str) -> GoalCommand:
    text = argument.strip()
    if text == 'stop':
        return GoalCommand(action='stop')
    if text == 'status':
        return GoalCommand(action='status')
    yolo = text == '--yolo' or text.startswith('--yolo ')
    if yolo:
        text = text[len('--yolo') :].strip()
    if not text:
        raise ValueError('Provide a goal after /goal.')
    return GoalCommand(action='set', text=text, yolo=yolo)


def goal_instructions(goal: Goal | None) -> str | None:
    """Return the session's active objective and its authorization boundary."""
    if goal is None or goal.status not in ('armed', 'active'):
        return None
    instruction = f'The user has set this goal for the session: {goal.text}'
    if goal.yolo:
        instruction += (
            '\nYOLO is active: plan and execute without asking the user for preferences or plan approval. '
            'Make reasonable decisions within the goal and existing agreements. Preserve all tool permissions '
            'and safety boundaries. Report a real blocker rather than inventing authorization.'
        )
    return instruction
