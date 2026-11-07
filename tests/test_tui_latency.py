"""The TUI must stay responsive while background diagnostics run."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

from src.engine.workspace.diff import DiffStat
from src.ui.activity_mixin import ActivityMixin


class DiffView(ActivityMixin):
    def __init__(self) -> None:
        self.root = '/workspace'
        self.painted: list[DiffStat | None] = []

    def _workspace_root(self) -> str:
        return self.root

    def query_one(self, selector: Any) -> Any:
        return SimpleNamespace(set_diff=self.painted.append)


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
        await _refresh(view)
        assert view.painted == [DiffStat(added=1)]

    monkeypatch.setattr('src.ui.activity_mixin.worktree_diff', read)
    asyncio.run(scenario())
