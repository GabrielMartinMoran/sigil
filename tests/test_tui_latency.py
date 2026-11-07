"""The TUI must stay responsive while background diagnostics run."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

from src.engine.workspace.diff import DiffStat
from src.ui.activity_mixin import ActivityMixin
from src.ui.widgets.status_bar import WorkspaceBadge


class DiffView(ActivityMixin):
    def __init__(self) -> None:
        self.root = '/workspace'
        self.painted: list[DiffStat | None] = []
        self.badges: list[WorkspaceBadge | None] = []
        self.badge_threads: list[int] = []

    def _workspace_root(self) -> str:
        return self.root

    def workspace_badge(self) -> WorkspaceBadge:
        self.badge_threads.append(threading.get_ident())
        return WorkspaceBadge(label=self.root, branch='current')

    def query_one(self, selector: Any) -> Any:
        return SimpleNamespace(set_diff=self.painted.append, set_workspace=self.badges.append)


async def _refresh(view: DiffView) -> None:
    await ActivityMixin.refresh_diff.__wrapped__(view)


def test_should_read_git_off_the_ui_thread(monkeypatch) -> None:
    threads: list[int] = []
    diff = DiffStat(added=2)

    def read(root: str) -> DiffStat:
        threads.append(threading.get_ident())
        return diff

    monkeypatch.setattr('src.ui.activity_mixin.worktree_diff', read)
    view = DiffView()
    asyncio.run(_refresh(view))
    assert threads and threads[0] != threading.get_ident()
    assert view.painted == [diff]
    assert view.badges == [WorkspaceBadge(label='/workspace', branch='current')]
    assert view.badge_threads and view.badge_threads[0] != threading.get_ident()


def test_should_coalesce_polls_and_discard_a_stale_workspace(monkeypatch) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def read(root: str) -> DiffStat:
        calls.append(root)
        started.set()
        assert release.wait(timeout=3)
        return DiffStat(added=1)

    async def scenario() -> None:
        view = DiffView()
        task = asyncio.create_task(_refresh(view))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            await _refresh(view)
            view.root = '/another-workspace'
        finally:
            release.set()
            await task
        assert calls == ['/workspace']
        assert view.painted == []
        assert view.badges == []
        await _refresh(view)
        assert view.painted == [DiffStat(added=1)]
        assert view.badges == [WorkspaceBadge(label='/another-workspace', branch='current')]

    monkeypatch.setattr('src.ui.activity_mixin.worktree_diff', read)
    asyncio.run(scenario())


def test_should_retry_the_status_read_after_a_git_failure(monkeypatch) -> None:
    view = DiffView()
    read_badge = Mock(side_effect=[RuntimeError('Git unavailable'), WorkspaceBadge(branch='recovered')])
    monkeypatch.setattr(view, 'workspace_badge', read_badge)
    monkeypatch.setattr('src.ui.activity_mixin.worktree_diff', lambda root: DiffStat())

    async def scenario() -> None:
        await _refresh(view)
        assert view.painted == []
        assert view.badges == []
        assert not view._diff_reading

        await _refresh(view)
        assert view.badges == [WorkspaceBadge(branch='recovered')]
        assert view.painted == [DiffStat()]

    asyncio.run(scenario())
