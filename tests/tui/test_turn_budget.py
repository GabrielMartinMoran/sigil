"""The turn's budget: a profile may declare how long its agent works.

The rule is one rule for both kinds of agent — `max_steps` in the frontmatter —
so the turn of the session has to read it too. The environment variable stays as
the fallback for every profile that says nothing, which is all of them by default.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.sessions.session_store import SessionStore
from src.engine.settings import SigilSettings
from src.ui.app import SigilApp


def _app(tmp: str, *, agents: dict[str, str] | None = None, active: str = 'wizard') -> SigilApp:
    """An app with a registry built from real profile files."""
    root = Path(tmp) / 'agents'
    root.mkdir(parents=True, exist_ok=True)
    for name, extra in (agents or {'wizard': ''}).items():
        (root / f'{name}.md').write_text(
            f'---\nname: {name}\ndescription: d\n{extra}---\nBody.\n',
            encoding='utf-8',
        )
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    registry = AgentRegistry([('project', root), ('builtin', Path(tmp) / 'none')], active=active)
    trees = AgentTreeStore()
    trees.bind(session, root_name=registry.active_name)
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=session,
        agent_registry=registry,
        agent_tree=trees,
    )


def test_without_a_declared_budget_the_setting_applies(tmp_path: str) -> None:
    async def inner() -> int:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app._settings = SigilSettings(max_steps=17)
                return app._turn_budget()

    assert asyncio.run(inner()) == 17


def test_the_profile_budget_wins_over_the_setting(tmp_path: str) -> None:
    agents = {'wizard': 'max_steps: 4\n'}

    async def inner() -> int:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, agents=agents)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app._settings = SigilSettings(max_steps=17)
                return app._turn_budget()

    assert asyncio.run(inner()) == 4


def test_switching_agent_switches_the_budget(tmp_path: str) -> None:
    """`/agent` changes who answers, and the budget is part of who answers."""
    agents = {'wizard': 'max_steps: 40\n', 'brief': 'max_steps: 3\n'}

    async def inner() -> tuple[int, int]:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, agents=agents)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                before = app._turn_budget()
                app.bind_agent('brief')
                await pilot.pause()
                return before, app._turn_budget()

    assert asyncio.run(inner()) == (40, 3)


def test_the_app_has_no_profile_to_read_when_the_registry_is_empty(tmp_path: str) -> None:
    """No profile means no declaration, and the setting still bounds the turn."""

    async def inner() -> int:
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app._agent_registry = None
                app._settings = SigilSettings(max_steps=9)
                return app._turn_budget()

    assert asyncio.run(inner()) == 9
