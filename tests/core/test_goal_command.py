"""The pure rules of ``/goal``, shared by the terminal and the service."""

from __future__ import annotations

from src.core.goal.command import DOCUMENTED_REFUSAL, NO_GOAL, apply_goal_command, describe_goal
from src.core.goal.state import Goal, parse_goal_command


def test_setting_a_first_goal_records_the_text_and_yolo() -> None:
    update = apply_goal_command(parse_goal_command('--yolo Ship the CSV export'), None, question_pending=False)

    assert update.ok
    assert update.changed
    assert update.goal == Goal(text='Ship the CSV export', yolo=True)
    assert 'Ship the CSV export' in update.notice
    assert 'YOLO' in update.notice


def test_replacing_a_goal_increments_the_revision_and_resets_progress() -> None:
    current = Goal(text='first', revision=4, continuations=3, status='completed')

    update = apply_goal_command(parse_goal_command('second'), current, question_pending=False)

    assert update.goal is not None
    assert update.goal.text == 'second'
    assert update.goal.revision == 5
    assert update.goal.continuations == 0
    assert update.goal.status == 'armed'


def test_stopping_a_goal_increments_the_revision_and_keeps_its_text() -> None:
    current = Goal(text='ship', revision=2, yolo=True, continuations=1, status='active')

    update = apply_goal_command(parse_goal_command('stop'), current, question_pending=False)

    assert update.changed
    assert update.goal is not None
    assert update.goal.text == 'ship'
    assert update.goal.status == 'stopped'
    assert update.goal.revision == 3
    assert update.goal.yolo is True


def test_stopping_without_a_goal_changes_nothing() -> None:
    update = apply_goal_command(parse_goal_command('stop'), None, question_pending=False)

    assert not update.changed
    assert update.goal is None
    assert update.notice == NO_GOAL


def test_status_reports_the_goal_without_changing_it() -> None:
    current = Goal(text='ship', revision=3, yolo=True, status='active')

    update = apply_goal_command(parse_goal_command('status'), current, question_pending=False)

    assert not update.changed
    assert update.goal is current
    assert 'ship' in update.notice
    assert 'active' in update.notice
    assert 'revision 3' in update.notice


def test_status_without_a_goal_says_so() -> None:
    update = apply_goal_command(parse_goal_command('status'), None, question_pending=False)

    assert update.notice == NO_GOAL
    assert describe_goal(None) == NO_GOAL


def test_activation_is_refused_while_a_question_waits() -> None:
    """A goal must never be the thing that answers a question."""
    current = Goal(text='old', revision=2, status='active')

    update = apply_goal_command(parse_goal_command('--yolo new objective'), current, question_pending=True)

    assert update.refused
    assert not update.changed
    assert update.goal is current
    assert update.notice == DOCUMENTED_REFUSAL
    assert 'question' in update.notice.lower()


def test_stop_while_a_question_waits_is_allowed() -> None:
    """Stopping touches the goal, not the waiting question."""
    current = Goal(text='old', revision=2, status='active')

    update = apply_goal_command(parse_goal_command('stop'), current, question_pending=True)

    assert not update.refused
    assert update.changed
    assert update.goal is not None
    assert update.goal.status == 'stopped'


def test_an_inactive_goal_is_still_described_as_it_is() -> None:
    goal = Goal(text='ship', status='stopped', revision=4)

    assert 'ship' in describe_goal(goal)
    assert 'stopped' in describe_goal(goal)
    assert 'revision 4' in describe_goal(goal)


def test_normal_goal_can_be_set_without_answering_a_pending_question() -> None:
    update = apply_goal_command(parse_goal_command('ship CSV'), None, question_pending=True)
    assert update.changed
    assert not update.refused
    assert update.goal == Goal(text='ship CSV')
