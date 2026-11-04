"""Test de integración del panel de roadmap en la app."""

import asyncio
import os
import tempfile
from pathlib import Path

os.environ.setdefault('LLM_MODEL', 'test/model')
os.environ.setdefault('OPENROUTER_API_KEY', 'test-key')

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import src.ui.app as appmod
from src.engine.roadmap.store import RoadmapStore, TodoItem
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.roadmap_panel import RoadmapPanel


async def _fake_fetch(model_id):
    return None


def test_roadmap_panel_shows_and_updates(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            roadmap = RoadmapStore(path=Path(tmp) / 'roadmap.json')
            roadmap.set_items([TodoItem(title='Tarea A', status='in_progress')])

            app = SigilApp(
                agent=Agent(TestModel(), instructions='x'),
                store=store,
                session=session,
                roadmap_store=roadmap,
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                panel = app.query_one(RoadmapPanel)
                assert panel.display is True
                assert 'Tarea A' in panel.render().plain

                roadmap.set_items([TodoItem(title='Tarea B', status='done')])
                await pilot.pause()
                assert 'Tarea B' in panel.render().plain

    asyncio.run(inner())
