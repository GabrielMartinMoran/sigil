"""Tests for the components that re-point their root on a workspace switch."""

from __future__ import annotations

import pytest

from src.engine.context.instructions.workspace_instructions import WorkspaceInstructions
from src.engine.context.scope import ScopeTracker
from src.engine.roadmap.stores import RoadmapStores
from src.engine.sessions.session_store import SessionStore
from src.engine.skills.registry import SkillRegistry

# --- ScopeTracker -------------------------------------------------------------


def test_scope_rebind_moves_the_root_and_forgets_the_directories(tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    (second / 'nested').mkdir(parents=True)
    (first).mkdir()

    scope = ScopeTracker(first)
    scope.observe('read_file', {'path': str(second / 'nested' / 'file.txt')})
    assert scope.scopes() == (first,)

    scope.rebind(second)
    assert scope.root == second
    # The observed directory belonged to the old workspace: the scope starts over.
    assert scope.relative_scopes() == ('.',)


def test_scope_rebind_to_the_same_root_is_a_no_op(tmp_path):
    scope = ScopeTracker(tmp_path)
    (tmp_path / 'a').mkdir()
    scope.observe('read_file', {'path': 'a/file.txt'})

    scope.rebind(tmp_path)
    assert scope.relative_scopes() == ('.', 'a')


# --- WorkspaceInstructions ----------------------------------------------------


def test_instructions_rebind_reads_the_new_tree(tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    (first / 'AGENTS.md').write_text('rules of the first\n')
    (second / 'AGENTS.md').write_text('rules of the second\n')

    instructions = WorkspaceInstructions(first)
    assert 'rules of the first' in instructions.render([first])

    instructions.rebind(second)
    assert instructions.root == second
    assert 'rules of the second' in instructions.render([second])
    assert 'rules of the first' not in instructions.render([second])


def test_instructions_rebind_clears_the_file_cache(tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    first.mkdir()
    second.mkdir()
    (first / 'AGENTS.md').write_text('a\n')

    instructions = WorkspaceInstructions(first)
    instructions.render([first])
    assert instructions._cache  # the read populated the cache

    instructions.rebind(second)
    assert instructions._cache == {}


# --- SessionStore -------------------------------------------------------------


def test_session_store_rebind_moves_the_workspace_row(tmp_path):
    """One database, one workspace row per directory: switching re-points the row."""
    store = SessionStore(cwd=tmp_path / 'a', base_dir=tmp_path / 'store')
    old_workspace = store.workspace_id

    store.rebind(tmp_path / 'b')

    assert store.cwd == str(tmp_path / 'b')
    assert store.workspace_id != old_workspace
    assert store.database.value('SELECT path FROM workspaces WHERE id = ?', (store.workspace_id,)) == str(
        tmp_path / 'b'
    )


def test_session_store_rebind_isolates_the_sessions(tmp_path):
    store = SessionStore(cwd=tmp_path / 'a', base_dir=tmp_path / 'store')
    store.create_session()

    store.rebind(tmp_path / 'b')
    assert store.list_sessions() == []

    other = SessionStore(cwd=tmp_path / 'b', base_dir=tmp_path / 'store')
    assert other.list_sessions() == []


# --- RoadmapStore -------------------------------------------------------------


def test_a_plan_belongs_to_an_execution_and_survives_a_workspace_switch(tmp_path):
    """There is no directory to move: the plan is keyed by the execution.

    Switching workspace changes the session's workspace row, not the identity of the
    plan, so a plan that was open keeps its items.
    """
    store = SessionStore(cwd=tmp_path / 'first', base_dir=tmp_path / 'home')
    session = store.create_session()
    plans = RoadmapStores(session_id=session.id, base_dir=tmp_path / 'home')
    from src.engine.roadmap.models.todo_item import TodoItem

    plans.root.set_items([TodoItem(title='kept across the switch')])

    store.rebind(tmp_path / 'second')

    # The same session, the same execution, the same plan.
    assert session.root_id == plans.root.execution_id
    assert [item.title for item in plans.root.snapshot()] == ['kept across the switch']


def test_a_plan_does_not_leak_between_workspaces(tmp_path):
    """Two workspaces are two sessions, and each one has its own plan."""
    from src.engine.roadmap.models.todo_item import TodoItem

    home = tmp_path / 'home'
    first = SessionStore(cwd=tmp_path / 'first', base_dir=home)
    first_session = first.create_session()
    RoadmapStores(session_id=first_session.id, base_dir=home).root.set_items([TodoItem(title='of the first')])

    second = SessionStore(cwd=tmp_path / 'second', base_dir=home)
    second_session = second.create_session()
    plans = RoadmapStores(session_id=second_session.id, base_dir=home)

    assert plans.root.snapshot() == []
    assert first.list_sessions()[0].id != second.list_sessions()[0].id


# --- SkillRegistry ------------------------------------------------------------


def _write_skill(root, name: str) -> None:
    directory = root / '.agents' / 'skills' / name
    directory.mkdir(parents=True)
    (directory / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: from {root.name}\n---\nbody\n')


def test_skill_registry_rebind_rescans_the_project_root(tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    _write_skill(first, 'alpha')
    _write_skill(second, 'beta')

    registry = SkillRegistry(
        roots=[('project', first / '.agents' / 'skills'), ('global', tmp_path / 'nope')],
    )
    assert [skill.name for skill in registry.list()] == ['alpha']
    # An explicit root list is not derived from the cwd, so it is left alone.
    registry.rebind(second)
    assert [skill.name for skill in registry.list()] == ['alpha']


def test_skill_registry_with_default_roots_follows_the_workspace(tmp_path, monkeypatch):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    _write_skill(first, 'alpha')
    _write_skill(second, 'beta')

    monkeypatch.chdir(first)
    monkeypatch.setenv('SIGIL_SKILLS_DIRS', '')
    registry = SkillRegistry()
    assert registry._project_root_is_derived is True
    assert 'alpha' in [skill.name for skill in registry.list()]

    registry.rebind(second)
    names = [skill.name for skill in registry.list()]
    assert 'beta' in names
    assert 'alpha' not in names


def test_skill_registry_keeps_an_explicit_skills_dir(tmp_path, monkeypatch):
    root = tmp_path / 'skills'
    _write_skill(tmp_path, 'alpha')
    monkeypatch.setenv('SIGIL_SKILLS_DIRS', str(root))

    registry = SkillRegistry()
    assert registry._project_root_is_derived is False
    registry.rebind(tmp_path / 'elsewhere')
    assert registry.roots == (('custom', root),)


@pytest.mark.parametrize('size', [1])
def test_registry_length_is_the_discovered_count(tmp_path, size: int):
    _write_skill(tmp_path, 'only')
    registry = SkillRegistry(roots=[('project', tmp_path / '.agents' / 'skills')])
    assert len(registry) == size
