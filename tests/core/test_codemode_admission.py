"""Concurrent sandboxes have a shared, bounded admission policy."""

import asyncio

import pytest

from src.core.codemode import sandbox


def test_sandbox_admission_is_shared_and_released_after_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        entered = asyncio.Queue()
        release = asyncio.Event()

        async def execute(*args, **kwargs):
            await entered.put(True)
            await release.wait()
            return 'done'

        monkeypatch.setattr(sandbox, '_execute_python', execute)
        first = asyncio.create_task(sandbox.execute_python('first'))
        second = asyncio.create_task(sandbox.execute_python('second'))
        await asyncio.wait_for(entered.get(), 1)
        await asyncio.wait_for(entered.get(), 1)
        with pytest.raises(sandbox.SandboxUnavailable, match='concurr'):
            await sandbox.execute_python('third')
        release.set()
        assert await asyncio.gather(first, second) == ['done', 'done']
        assert await sandbox.execute_python('fourth') == 'done'

    asyncio.run(run())
