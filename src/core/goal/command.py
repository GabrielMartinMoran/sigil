"""Apply one parsed ``/goal`` command to a session's goal.

The parser lives in :mod:`src.core.goal.state`; this module is the single place that
decides what a command does to the goal value, so the in-process terminal and the
attached service's runtime hook cannot drift apart. It is pure: persistence and
publication belong to the caller that owns the session.

Two rules are encoded here on purpose:

- **A pending question is never answered by a goal.** A normal goal can be set
  without affecting the question. YOLO activation is refused until the question
  is resolved because cancelling it would interrupt the active run.
- **Replacing or stopping a goal increments its revision.** The revision is how the
  runtime recognises that a review or a continuation decided for a goal that no
  longer exists, so every state-changing command invalidates the old one.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from src.core.goal.state import Goal, GoalCommand

DOCUMENTED_REFUSAL = 'A question is waiting: answer or dismiss it before activating YOLO.'
"""What activation says while a question is pending.

The command changed nothing, so the notice states the refusal and the next action
instead of painting a goal that was never stored.
"""

NO_GOAL = 'No goal is set.'


@dataclass(frozen=True, slots=True)
class GoalUpdate:
    """What one command produced, before the caller stores and publishes it."""

    goal: Goal | None
    notice: str
    changed: bool = False
    refused: bool = False

    @property
    def ok(self) -> bool:
        return not self.refused


def apply_goal_command(command: GoalCommand, current: Goal | None, *, question_pending: bool) -> GoalUpdate:
    """Return the goal after ``command`` and the line the user reads.

    ``question_pending`` is required, not optional: a caller that has not decided
    whether the session is waiting on an answer must not activate YOLO by accident.
    The runtime passes its pending-question fact; the terminal passes the panel's.
    """
    if command.action == 'status':
        return GoalUpdate(goal=current, notice=describe_goal(current))

    if command.action == 'stop':
        if current is None:
            return GoalUpdate(goal=None, notice=NO_GOAL)
        stopped = replace(current, status='stopped', revision=current.revision + 1)
        return GoalUpdate(
            goal=stopped, notice=f'Goal stopped (revision {stopped.revision}): {stopped.text}', changed=True
        )

    if question_pending and command.yolo:
        return GoalUpdate(goal=current, notice=DOCUMENTED_REFUSAL, refused=True)

    goal = (
        current.replace(command.text, yolo=command.yolo)
        if current is not None
        else Goal(text=command.text, yolo=command.yolo)
    )
    mode = ' · YOLO' if goal.yolo else ''
    return GoalUpdate(goal=goal, notice=f'Goal set (revision {goal.revision}{mode}): {goal.text}', changed=True)


def describe_goal(goal: Goal | None) -> str:
    """One line with the whole goal, or the honest answer that there is none."""
    if goal is None:
        return NO_GOAL
    parts = [goal.status, f'revision {goal.revision}']
    if goal.continuations:
        parts.append(f'{goal.continuations} continuation{"s" if goal.continuations != 1 else ""}')
    if goal.yolo:
        parts.append('YOLO')
    return f'Goal ({" · ".join(parts)}): {goal.text}'


__all__ = [
    'DOCUMENTED_REFUSAL',
    'NO_GOAL',
    'GoalUpdate',
    'apply_goal_command',
    'describe_goal',
]
