"""Git identity in the status bar, including service projections."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.workspace.models.diff_stat import DiffStat
from src.core.workspace.models.workspace import Workspace
from src.core.workspace.models.worktree import Worktree
from src.server.api.views import workspace_view
from src.ui.mixins.activity_mixin import ActivityMixin
from src.ui.widgets.panels.status_bar import StatusBar


@pytest.mark.parametrize('attached', [False, True])
@pytest.mark.parametrize(
    ('worktree', 'expected'),
    [
        (Worktree(path=Path('/repo'), branch='develop', is_main=True), 'develop'),
        (Worktree(path=Path('/fix'), branch='feature/fix'), 'feature/fix (fix)'),
        (Worktree(path=Path('/repo'), head='abcdef12345', is_main=True), 'detached @abcdef1'),
        (Worktree(path=Path('/fix@home'), head='abcdef12345'), 'detached @abcdef1 (fix@home)'),
        (None, ''),
    ],
)
def test_git_identity_from_workspace(attached: bool, worktree: Worktree | None, expected: str) -> None:
    workspace = Workspace(root=Path('/repo'), worktree=worktree)
    view = ActivityMixin()
    if attached:
        view._service_state = {'workspace': workspace_view(workspace)}
    else:
        view._workspace_manager = SimpleNamespace(current=lambda: workspace)
    bar = StatusBar()
    bar.set_workspace(view.workspace_badge())
    assert bar._right().plain == expected
    if worktree is not None:
        bar.set_diff(DiffStat(added=12, removed=3, untracked=2))
        assert bar._right().plain == f'{expected}  +12 −3  2 new'


def test_branch_is_highlighted_and_worktree_is_muted() -> None:
    from src.ui.widgets.models.workspace_badge import WorkspaceBadge

    bar = StatusBar()
    bar.set_workspace(WorkspaceBadge(label='fix', branch='feature/fix', is_worktree=True))
    text = bar._right()
    assert [(text.plain[span.start : span.end], span.style) for span in text.spans] == [
        ('feature/fix', 'bold cyan'),
        (' (fix)', 'bright_black'),
    ]
