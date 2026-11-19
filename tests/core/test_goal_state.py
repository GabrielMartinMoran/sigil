"""Goal command parsing and lifecycle rules."""

import pytest

from src.core.goal.state import Goal, parse_goal_command


def test_goal_command_preserves_text_and_selects_yolo() -> None:
    command = parse_goal_command('--yolo Ship the CSV export')
    assert command.action == 'set'
    assert command.text == 'Ship the CSV export'
    assert command.yolo is True


def test_goal_command_recognizes_management_commands() -> None:
    assert parse_goal_command('stop').action == 'stop'
    assert parse_goal_command('status').action == 'status'


def test_goal_command_rejects_empty_objective() -> None:
    with pytest.raises(ValueError):
        parse_goal_command('--yolo')


def test_replacing_goal_invalidates_old_revision_and_resets_progress() -> None:
    goal = Goal(text='first', revision=2, continuations=3, status='completed')
    replacement = goal.replace('second', yolo=True)
    assert replacement.revision == 3
    assert replacement.text == 'second'
    assert replacement.yolo
    assert replacement.continuations == 0
    assert replacement.status == 'armed'


def test_goal_in_draft_session_survives_reload(tmp_path) -> None:
    from src.core.sessions.session_store import SessionStore

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path)
    session = store.draft_session()
    session.goal = Goal(text='Ship the export', yolo=True)
    store.save(session)

    restored = store.load(session.id)
    assert restored is not None
    assert restored.goal == session.goal


def test_yolo_instructions_preserve_permission_boundaries() -> None:
    from src.core.goal.state import goal_instructions

    instructions = goal_instructions(Goal('Ship the export', yolo=True))
    assert 'Ship the export' in instructions
    assert 'permissions' in instructions.lower()
    assert 'without asking' in instructions.lower()
    assert goal_instructions(Goal('old goal', status='completed')) is None


def test_goal_continuation_limit_is_configurable_and_positive() -> None:
    from src.core.config.load_settings import load_settings

    assert load_settings({}).goal_max_continuations == 10
    assert load_settings({'SIGIL_GOAL_MAX_CONTINUATIONS': '3'}).goal_max_continuations == 3
    with pytest.raises(ValueError):
        load_settings({'SIGIL_GOAL_MAX_CONTINUATIONS': '0'})
