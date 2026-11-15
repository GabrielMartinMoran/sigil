"""Grouping workspaces: one repository, its main checkout, its worktrees.

The distinction this file pins down is the one that was wrong: a linked worktree is a
**workspace** — its own root, its own sessions, its own tools — and the engine is right
to treat it as one. It is not a different **project**, which is what a flat list made it
look like. The projection groups them, and these tests hold the shape.
"""

from __future__ import annotations

from pathlib import Path

from src.engine.workspace.models.worktree import Workspace, Worktree
from src.server import views


def _workspace(path: str, *, branch: str | None = 'x', main: bool = False) -> Workspace:
    return Workspace(root=Path(path), worktree=Worktree(path=Path(path), branch=branch, is_main=main))


def test_the_main_checkout_is_not_a_worktree() -> None:
    """`is_worktree` is about being linked, so the main one is false even inside git."""
    main = _workspace('/repo/sigil', main=True)
    linked = _workspace('/repo/.sigil/worktrees/sigil/debug')

    assert views.workspace_view(main)['is_worktree'] is False
    assert views.workspace_view(main)['is_main'] is True
    assert views.workspace_view(linked)['is_worktree'] is True


def test_a_worktree_names_its_repository() -> None:
    """Without it, `better-debug` reads as a project; with it, as part of one."""
    view = views.workspace_view(_workspace('/repo/wt/improve'), repository='sigil')
    assert view['repository'] == 'sigil'
    assert view['label'] == 'improve'


def test_worktrees_of_one_repository_are_one_group() -> None:
    found = [
        _workspace('/repo/sigil', branch='main', main=True),
        _workspace('/repo/.sigil/worktrees/sigil/debug'),
        _workspace('/repo/.sigil/worktrees/sigil/docs'),
    ]

    groups = views.repositories_view('sigil', '/repo/sigil', found, active='/repo/sigil')

    assert len(groups) == 1
    assert groups[0]['name'] == 'sigil'
    assert groups[0]['root'] == '/repo/sigil'
    assert len(groups[0]['workspaces']) == 3


def test_the_main_checkout_comes_first() -> None:
    """Ordering is a fact of the projection, not of how git lists worktrees."""
    found = [
        _workspace('/repo/.sigil/worktrees/sigil/zzz'),
        _workspace('/repo/.sigil/worktrees/sigil/aaa'),
        _workspace('/repo/sigil', branch='main', main=True),
    ]

    workspaces = views.repositories_view('sigil', '/repo/sigil', found, active='/repo/sigil')[0]['workspaces']

    assert workspaces[0]['is_main'] is True
    assert [item['label'] for item in workspaces[1:]] == ['aaa', 'zzz']


def test_the_active_workspace_is_marked() -> None:
    found = [
        _workspace('/repo/sigil', branch='main', main=True),
        _workspace('/repo/.sigil/worktrees/sigil/debug'),
    ]

    workspaces = views.repositories_view('sigil', '/repo/sigil', found, active='/repo/.sigil/worktrees/sigil/debug')[0][
        'workspaces'
    ]

    active = [item for item in workspaces if item['active']]
    assert len(active) == 1
    assert active[0]['label'] == 'debug'


def test_a_directory_outside_git_is_its_own_group() -> None:
    """No repository means no worktrees: the workspace is the project."""
    groups = views.repositories_view(None, None, [_workspace('/tmp/scratch', branch=None)], active='/tmp/scratch')

    assert len(groups) == 1
    assert groups[0]['name'] == 'scratch'
    assert groups[0]['root'] == '/tmp/scratch'
    assert groups[0]['workspaces'][0]['repository'] is None


def test_a_detached_worktree_says_so() -> None:
    """A detached HEAD is the one case where `branch` is empty, and it must not look
    like a broken workspace."""
    view = views.workspace_view(_workspace('/repo/wt/detached', branch=None))
    assert view['is_detached'] is True
    assert view['branch'] is None


def test_a_session_row_says_which_worktree_it_lives_in() -> None:
    """The list of conversations spans worktrees; a row that does not say which one is
    ambiguous, because the title alone does not identify a checkout."""
    from src.engine.sessions.session_store import SessionSummary

    summary = SessionSummary(
        id='a1b2c3d4',
        workspace='/repo/.sigil/worktrees/sigil/debug',
        title='t',
        preview='',
        updated_at='2026-11-07T10:00:00+00:00',
        profile='wizard',
    )

    view = views.summary_view(summary, repository='sigil', is_worktree=True)

    assert view['repository'] == 'sigil'
    assert view['is_worktree'] is True
