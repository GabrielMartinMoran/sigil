"""Provider selections retain model capabilities across real storage reads."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from src.engine.sessions.session_store import SessionStore
from tests.support.provider_setup import _prepared


def test_the_selection_survives_being_written_and_read_by_the_store():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service = await _prepared(root)
            store = SessionStore(cwd=str(root), base_dir=root / 'sessions')
            service.select('work', 'second/model', 'high')

            session = store.create_session()
            session.selection = service.snapshot().selection
            store.save(session)

            reloaded = store.load(session.id)
            assert reloaded is not None
            assert reloaded.selection is not None
            assert reloaded.selection.account_id == service.list_accounts()[0].id
            assert reloaded.selection.model == 'second/model'
            assert reloaded.selection.thinking == 'high'
            # The window and the effort levels travel with the selection so a cold
            # start shows the real context size instead of `?`.
            assert reloaded.selection.window == 200_000
            assert reloaded.selection.efforts == ('low', 'medium', 'high')

    asyncio.run(inner())
