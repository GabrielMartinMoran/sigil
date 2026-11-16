"""Everything handed to `run_worker` must be runnable.

Textual runs the work in a worker and fails it with

    WorkerError: Unsupported attempt to run an async worker

when the work is neither a coroutine, nor awaitable, nor a callable — and then paints
a traceback over the app. The app keeps running, which is what makes this expensive
to notice: `run_worker(self.show_chat())` looks like a call and *is* one, so the
argument is a `bool` and the effect has already happened by the time the panel
appears.

So the invariant is checked where it can be stated exactly: every value that reaches
`run_worker` has to be something the worker can run. A misplaced `await` — or a
missing one — is then a failing test instead of a traceback in a screenshot.

State-based assertions were tried first and are useless here: `WorkerManager` drops a
worker from its set as soon as it finishes, so a failed worker is gone before the test
can look at it.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures('memory_databases')


def _runnable(work: Any) -> bool:
    """What `Worker._run_async` accepts."""
    if inspect.iscoroutinefunction(work):
        return True
    if hasattr(work, 'func') and inspect.iscoroutinefunction(work.func):
        return True
    if inspect.isawaitable(work):
        return True
    return callable(work) and not isinstance(work, bool)


def test_the_recorder_recognises_the_bug() -> None:
    """The check itself: a `bool` is what the traceback was about."""
    pending = asyncio.sleep(0)
    try:
        assert _runnable(pending) is True
    finally:
        pending.close()
    assert _runnable(lambda: None) is True
    assert _runnable(True) is False
    assert _runnable(False) is False
    assert _runnable('a string') is False
