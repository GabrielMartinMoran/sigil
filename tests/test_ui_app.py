"""Smoke tests de la app Textual (sin red ni modelo real)."""

import asyncio
import os
import tempfile
from pathlib import Path

# ConfigProvider lee estas variables al importarse; las definimos antes.
os.environ.setdefault('LLM_MODEL', 'test/model')
os.environ.setdefault('OPENROUTER_API_KEY', 'test-key')

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import src.ui.app as appmod
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.command_menu import CommandMenu
from src.ui.widgets.prompt_input import PromptInput


async def _fake_fetch(model_id):
    return None


async def _wait_idle(app, pilot, attempts: int = 40) -> None:
    for _ in range(attempts):
        await pilot.pause()
        if not app.query_one(PromptInput).disabled:
            return


def _make_app(agent: Agent, tmp: str) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    return SigilApp(agent=agent, store=store, session=session)


def test_app_runs_turn_and_renders_chat(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='respuesta'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'o', 'l', 'a')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                chat = app.query_one(ChatLog)
                assert len(chat.children) >= 2  # mensaje del usuario + respuesta
                assert len(app._session.messages) == 2

    asyncio.run(inner())


def test_command_menu_filters_and_selects(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='ok'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                app.query_one(PromptInput).value = ''
                await pilot.press('slash')
                await pilot.pause()

                menu = app.query_one(CommandMenu)
                assert menu.display is True
                assert menu.option_count == 4

                await pilot.press('n')
                await pilot.pause()
                assert menu.option_count == 1
                assert menu.get_option_at_index(0).id == '/new'

                await pilot.press('enter')
                await pilot.pause()
                assert menu.display is False

    asyncio.run(inner())


def test_tool_call_and_result_are_rendered(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(call_tools=['ping']), instructions='x')

            @agent.tool_plain
            def ping() -> str:
                return 'pong'

            app = _make_app(agent, tmp)
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                classes = [w.__class__.__name__ for w in app.query_one(ChatLog).children]
                assert 'Static' in classes  # incluye líneas de tool call/result
                assert len(app._session.messages) >= 3

    asyncio.run(inner())


def test_autoscroll_only_when_at_bottom(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(Agent(TestModel(), instructions='x'), tmp)
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)

                for index in range(60):
                    await chat.add_user(f'mensaje {index}')
                await pilot.pause()
                assert chat.is_vertical_scroll_end

                chat.scroll_home(animate=False)
                await pilot.pause()
                assert not chat.is_vertical_scroll_end
                y = chat.scroll_offset.y

                await chat.add_user('nuevo con scroll arriba')
                await pilot.pause()
                assert chat.scroll_offset.y == y  # no salta al fondo

    asyncio.run(inner())


def test_markdown_output_is_rendered_in_app(monkeypatch):
    monkeypatch.setattr(appmod, 'fetch_model_info', _fake_fetch)

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            agent = Agent(TestModel(custom_output_text='# Encabezado\n\nEsto es **negrita**.'), instructions='x')
            app = _make_app(agent, tmp)
            async with app.run_test(size=(90, 30)) as pilot:
                await pilot.pause()
                await pilot.press('h', 'i')
                await pilot.press('enter')
                await _wait_idle(app, pilot)

                svg = app.export_screenshot()
                assert 'Encabezado' in svg
                assert '# Encabezado' not in svg  # el '#' de markdown no se muestra literal
                assert 'negrita' in svg
                assert '**negrita**' not in svg  # los '**' no se muestran literales

    asyncio.run(inner())
