"""Desktop alerts are discoverable and configurable from the composer."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from src.engine.commands import CommandOwner, match_commands
from src.engine.preferences import Preferences, PreferencesStore
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.help_text import HELP_TEXT
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from tests.ui_helpers import render_text


def make_app(tmp_path: Path) -> SigilApp:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
        preferences_store=PreferencesStore(tmp_path / 'ui.json'),
    )


def test_should_expose_alerts_in_client_menu_and_help() -> None:
    matches = match_commands('/alerts')
    assert len(matches) == 1
    assert matches[0].owner is CommandOwner.CLIENT
    assert '/alerts' in HELP_TEXT


def test_should_persist_alert_preference_without_changing_other_preferences(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app._preferences = Preferences(theme='dark', reasoning_visible=True, tools_expanded=True)
        async with app.run_test():
            app._turn_active = True
            await app.handle_command('/alerts', 'off')
            expected = Preferences(reasoning_visible=True, tools_expanded=True, desktop_notifications=False)
            assert app._preferences == expected
            assert app._preferences_store.load() == expected
            await app.handle_command('/alerts', 'on')
            assert app._preferences.desktop_notifications
            assert app._preferences_store.load().desktop_notifications
            app._turn_active = False

    asyncio.run(scenario())


def test_should_show_status_without_sending_or_mutating(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        sender = Mock()
        monkeypatch.setattr('src.ui.app.send_notification', sender)
        monkeypatch.setattr('src.ui.preferences_mixin.notification_command', lambda _: None)
        async with app.run_test():
            await app.handle_command('/alerts')
            text = '\n'.join(render_text(child.render()) for child in app.query_one(ChatLog).children)
            assert 'enabled' in text
            assert 'unavailable' in text
            assert '/alerts test' not in text
            assert 'test checks delivery' not in HELP_TEXT
            sender.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.parametrize('argument', ['test', 'nonsense'])
def test_should_reject_diagnostic_and_unknown_arguments_without_sending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argument: str
) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        sender = Mock()
        monkeypatch.setattr('src.ui.app.send_notification', sender)
        async with app.run_test() as pilot:
            field = app.query_one(PromptInput)
            field.value = f'/alerts {argument}'
            await field.action_submit()
            await pilot.pause()
            await app.workers.wait_for_complete()
            text = '\n'.join(render_text(child.render()) for child in app.query_one(ChatLog).children)
            assert 'Usage: /alerts [on|off]' in text
            assert 'Usage: /alerts [on|off|test]' not in text
            sender.assert_not_called()
            assert app._preferences.desktop_notifications
            assert app._session.messages == []

    asyncio.run(scenario())
