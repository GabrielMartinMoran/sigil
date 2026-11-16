"""Timeouts cover sandbox setup and the whole script invocation."""

import asyncio
import inspect

from src.core.codemode import sandbox


def test_default_timeout_is_high_and_explicit() -> None:
    assert inspect.signature(sandbox.execute_python).parameters['timeout_seconds'].default == 120


def test_timeout_covers_sandbox_setup_and_releases_admission(monkeypatch) -> None:
    async def run() -> None:
        cancelled = asyncio.Event()

        async def stalled(*args, **kwargs):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        monkeypatch.setattr(sandbox, '_execute_python', stalled)
        result = await asyncio.wait_for(sandbox.execute_python('pass', timeout_seconds=0.1), 2)
        assert result.timed_out
        assert cancelled.is_set()
        assert sandbox._ADMISSION.acquire(blocking=False)
        sandbox._ADMISSION.release()

    asyncio.run(run())


def test_timeout_cancels_an_active_nested_call() -> None:
    async def run() -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class Dispatcher:
            async def call(self, name, args):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        task = asyncio.create_task(
            sandbox.execute_python("await tools.read_file(path='test')", timeout_seconds=3, dispatcher=Dispatcher())
        )
        await asyncio.wait_for(started.wait(), 2)
        result = await asyncio.wait_for(task, 5)
        assert result.timed_out
        assert cancelled.is_set()

    asyncio.run(run())
