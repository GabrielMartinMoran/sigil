import asyncio
import json
import sys

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual.widgets import OptionList

from src.engine.mcp.config.loader import load_config
from src.engine.mcp.deferred import MCPGateway
from src.engine.mcp.manager.manager import MCPManager
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.mcp_screen import MCPScreen
from src.ui.widgets.prompt_input import PromptInput
from tests.ui_helpers import wait_until


def test_only_enabled_servers_show_their_tools():
    from src.engine.mcp.tool_models.server_status import ServerStatus
    from src.ui.widgets.mcp_screen import _row

    status = ServerStatus(name='server', transport='stdio', enabled=True, prefix='', source='config', tools=['read'])
    assert 'read' in _row(status).plain
    assert 'read' not in _row(status.model_copy(update={'enabled': False})).plain


def test_mcp_changes_are_refused_while_a_subagent_runs(tmp_path):
    from types import SimpleNamespace

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    app = SigilApp(agent=Agent(TestModel()), store=store, session=store.create_session())
    app._summoner = SimpleNamespace(running=lambda: 0)
    assert not app.is_refused_while_busy('/mcp')
    app._summoner = SimpleNamespace(running=lambda: 1)
    assert app.is_refused_while_busy('/mcp')


def test_mcp_modal_scrolls_a_long_server_list(tmp_path):
    from textual.app import App

    from src.engine.mcp.tool_models.server_status import ServerStatus

    statuses = [
        ServerStatus(name=f'server-{i:02}', transport='stdio', enabled=False, prefix='', source='config')
        for i in range(40)
    ]

    async def toggle(name, enabled):
        return next(status.model_copy(update={'enabled': enabled}) for status in statuses if status.name == name)

    async def run():
        app = App()
        async with app.run_test(size=(100, 30)) as pilot:
            app.push_screen(MCPScreen(statuses, toggle))
            await pilot.pause()
            options = app.screen.query_one(OptionList)
            for _ in range(39):
                await pilot.press('down')
            assert options.highlighted == 39
            assert options.scroll_y > 0
            await pilot.press('enter')
            assert 'on' in options.get_option_at_index(39).prompt.plain

    asyncio.run(run())


def test_mcp_modal_loads_tools_for_enabled_servers_without_blocking_open(tmp_path):
    from tests.test_mcp_deferred import SERVER_SCRIPT

    script = tmp_path / 'server.py'
    script.write_text(SERVER_SCRIPT)
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'mcp_servers': {'probe': {'command': sys.executable, 'args': [str(script)]}}}))
    manager = MCPManager(load_config([config], environ={}).servers)
    gateway = MCPGateway(configured=1)
    gateway.install(manager)
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    app = SigilApp(agent=Agent(TestModel()), store=store, session=store.create_session(), mcp_manager=gateway)

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            app.query_one(PromptInput).value = '/mcp'
            await pilot.press('enter')
            await wait_until(pilot, lambda: isinstance(app.screen, MCPScreen))
            options = app.screen.query_one(OptionList)
            await wait_until(pilot, lambda: 'probe_add' in options.get_option_at_index(0).prompt.plain, timeout=10)
            await pilot.press('escape')

    asyncio.run(run())


def test_mcp_modal_toggles_and_persists_per_session(tmp_path):
    config = tmp_path / 'config.json'
    config.write_text(
        json.dumps(
            {
                'mcp_servers': {
                    'first': {'command': sys.executable, 'args': ['-c', 'pass']},
                    'second': {'command': sys.executable, 'args': ['-c', 'pass'], 'enabled': False},
                }
            }
        )
    )
    manager = MCPManager(load_config([config], environ={}).servers)
    gateway = MCPGateway(configured=2)
    gateway.install(manager)
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'store')
    session = store.create_session()
    app = SigilApp(agent=Agent(TestModel()), store=store, session=session, mcp_manager=gateway)

    async def run():
        async with app.run_test(size=(120, 40)) as pilot:
            app.query_one(PromptInput).value = '/mcp'
            await pilot.press('enter')
            await wait_until(pilot, lambda: isinstance(app.screen, MCPScreen))
            options = app.screen.query_one(OptionList)
            assert options.option_count == 2
            await pilot.press('enter', 'down', 'enter')
            assert session.mcp_overrides == {'first': False, 'second': True}
            assert not manager.get('first').enabled and manager.get('second').enabled
            assert store.load(session.id).mcp_overrides == session.mcp_overrides
            await pilot.press('escape')
            other = store.create_session()
            app.bind_session(other)
            assert manager.get('first').enabled and not manager.get('second').enabled
            app.bind_session(store.load(session.id))
            assert not manager.get('first').enabled and manager.get('second').enabled

    asyncio.run(run())
