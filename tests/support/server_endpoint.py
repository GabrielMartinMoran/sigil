"""Shared server endpoint test support."""

from __future__ import annotations

import asyncio
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock


@contextmanager
def _umask(mask: int) -> Iterator[None]:
    """A permissive umask, to prove the modes do not come from it."""

    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


class _RuntimeStub:
    """Only the pieces `create_app` touches around the lifespan; no route is called."""

    def __init__(self) -> None:
        from src.server.events import EventHub

        self.hub = EventHub()
        self.composition = MagicMock()
        self.composition.extensions.close = AsyncMock()
        self.session_runtime = MagicMock()
        self.session_runtime.aclose = AsyncMock()
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append('start')

    def ensure_workers(self) -> None:
        self.calls.append('ensure_workers')

    def abort(self) -> None:
        self.calls.append('abort')

    async def wait(self) -> None:
        self.calls.append('wait')

    async def reap_sessions(self, **kwargs: object) -> None:
        await asyncio.Event().wait()

    async def close_sessions(self) -> None:
        self.abort()
        await self.wait()
        try:
            await self.session_runtime.aclose()
        finally:
            await self.composition.extensions.close()

    def close(self) -> None:
        self.calls.append('close')
