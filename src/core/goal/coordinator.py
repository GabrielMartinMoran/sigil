"""Eligibility rules for reviewing a goal after all work has settled."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.goal.state import Goal


@dataclass(frozen=True, slots=True)
class GoalActivity:
    turn_running: bool = False
    queued_input: bool = False
    subagent_running: bool = False
    pending_report: bool = False
    question_pending: bool = False
    cancelled: bool = False

    @property
    def settled(self) -> bool:
        return not (
            self.turn_running
            or self.queued_input
            or self.subagent_running
            or self.pending_report
            or self.question_pending
            or self.cancelled
        )


def should_review(goal: Goal | None, activity: GoalActivity) -> bool:
    return bool(goal and goal.status == 'active' and activity.settled)


def may_apply_review(goal: Goal | None, revision: int, activity: GoalActivity) -> bool:
    return bool(goal and goal.revision == revision and should_review(goal, activity))


class ProgressTracker:
    """Recognize exact repeated review evidence without guessing semantic progress."""

    def __init__(self) -> None:
        self._last: tuple[str, str, str] | None = None
        self._count = 0

    def stalled(self, report: str, roadmap: str, reason: str) -> bool:
        evidence = (report, roadmap, reason)
        self._count = self._count + 1 if evidence == self._last else 1
        self._last = evidence
        return self._count >= 3
