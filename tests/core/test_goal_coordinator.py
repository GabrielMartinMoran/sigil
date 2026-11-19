"""Review eligibility and stale-decision protection."""

from src.core.goal.coordinator import GoalActivity, may_apply_review, should_review
from src.core.goal.state import Goal


def test_review_only_when_goal_has_worked_and_session_is_fully_quiet() -> None:
    goal = Goal('ship', status='active')
    assert should_review(goal, GoalActivity())
    assert not should_review(Goal('ship'), GoalActivity())
    for activity in (
        GoalActivity(turn_running=True),
        GoalActivity(queued_input=True),
        GoalActivity(subagent_running=True),
        GoalActivity(pending_report=True),
        GoalActivity(question_pending=True),
        GoalActivity(cancelled=True),
    ):
        assert not should_review(goal, activity)


def test_stale_review_cannot_continue_replaced_goal_or_new_work() -> None:
    goal = Goal('ship', status='active', revision=3)
    assert may_apply_review(goal, 3, GoalActivity())
    assert not may_apply_review(goal.replace('different'), 3, GoalActivity())
    assert not may_apply_review(goal, 3, GoalActivity(queued_input=True))
    assert not may_apply_review(None, 3, GoalActivity())


def test_repeated_identical_evidence_stops_after_three_reviews() -> None:
    from src.core.goal.coordinator import ProgressTracker

    tracker = ProgressTracker()
    assert not tracker.stalled('same report', 'same roadmap', 'run tests')
    assert not tracker.stalled('same report', 'same roadmap', 'run tests')
    assert tracker.stalled('same report', 'same roadmap', 'run tests')
    assert not tracker.stalled('new report', 'same roadmap', 'run tests')
