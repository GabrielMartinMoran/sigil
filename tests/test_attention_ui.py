"""Attention events are emitted at user-visible lifecycle boundaries."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual.widgets._toast import Toast

from src.engine.interaction.models.question_item import QuestionItem, QuestionOption
from src.engine.preferences import Preferences
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.desktop_notifications import Attention, NotificationContext
from src.ui.widgets.prompt_input import PromptInput


def make_app(tmp_path: Path) -> SigilApp:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'), store=store, session=store.create_session()
    )


def test_should_notify_once_after_turn_is_saved(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app.notify_attention = Mock()
        async with app.run_test() as pilot:
            app.run_turn('hello')
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.notify_attention.assert_called_once_with(Attention.FINISHED)
            assert app._store.load(app._session.id).messages

    asyncio.run(scenario())


@pytest.mark.parametrize('event', [Attention.FINISHED, Attention.FAILED])
def test_should_hold_attention_while_a_subagent_still_works(tmp_path: Path, event: Attention) -> None:
    """The turn is over, the squad is not: the user is not done being waited for."""

    async def scenario() -> None:
        app = make_app(tmp_path)
        app.notify_attention = Mock()
        running = {'count': 1}
        app.subagents_running = lambda: running['count']
        async with app.run_test():
            app.settle_attention(event)
            app.notify_attention.assert_not_called()
            # The last subagent finishes; the next turn ends with nothing running.
            running['count'] = 0
            app.settle_attention(event)
            app.notify_attention.assert_called_once_with(event)

    asyncio.run(scenario())


def test_should_delay_the_notification_until_the_last_subagent_finishes(tmp_path: Path) -> None:
    """Wiring, not just the policy: the end of a turn is what asks for the signal."""

    async def scenario() -> None:
        app = make_app(tmp_path)
        app.notify_attention = Mock()
        running = {'count': 1}
        app.subagents_running = lambda: running['count']
        async with app.run_test() as pilot:
            app.run_turn('hello')
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.notify_attention.assert_not_called()
            running['count'] = 0
            app.run_turn('and now?')
            await app.workers.wait_for_complete()
            await pilot.pause()
            app.notify_attention.assert_called_once_with(Attention.FINISHED)

    asyncio.run(scenario())


def test_should_notify_for_question_but_not_empty_batch(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        app.notify_attention = Mock()
        async with app.run_test() as pilot:
            assert await app.ask_question([]) == []
            app.notify_attention.assert_not_called()
            task = asyncio.create_task(
                app.ask_question(
                    [
                        QuestionItem(
                            question='Private text', options=[QuestionOption(label='Yes'), QuestionOption(label='No')]
                        )
                    ]
                )
            )
            await pilot.pause()
            app.notify_attention.assert_called_once_with(Attention.QUESTION)
            await pilot.press('enter')
            assert await asyncio.wait_for(task, 2) is not None

    asyncio.run(scenario())


@pytest.mark.parametrize('size', [(100, 35), (60, 18)])
def test_should_keep_toasts_above_composer_without_stealing_focus(tmp_path: Path, size: tuple[int, int]) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        async with app.run_test(size=size, notifications=True) as pilot:
            prompt = app.query_one(PromptInput)
            prompt.value = 'draft'
            prompt.focus()
            app.notify('Connected', title='MCP')
            await pilot.pause()
            toast = app.screen.query_one(Toast)
            assert toast.region.y < size[1] // 2
            assert toast.region.bottom < prompt.region.y
            assert app.focused is prompt
            assert prompt.value == 'draft'

    asyncio.run(scenario())


def test_should_not_deliver_desktop_notifications_in_headless_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        send = Mock()
        monkeypatch.setattr('src.ui.app.send_notification', send)
        async with app.run_test():
            app.notify_attention(Attention.FINISHED)
            await app.workers.wait_for_complete()
            send.assert_not_called()

    asyncio.run(scenario())


def test_should_allow_disabling_desktop_notifications_in_preferences() -> None:
    assert Preferences().desktop_notifications
    preferences = Preferences.from_dict({'desktop_notifications': False})
    assert not preferences.desktop_notifications
    assert preferences.to_dict()['desktop_notifications'] is False


@pytest.mark.parametrize('cancelled', [False, True])
def test_should_notify_failure_but_not_user_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    from pydantic_ai.exceptions import RunCancelled

    async def scenario() -> None:
        app = make_app(tmp_path)
        app.notify_attention = Mock()
        error = RunCancelled('stopped') if cancelled else RuntimeError('private error')
        monkeypatch.setattr(app._agent, 'iter', Mock(side_effect=error))
        async with app.run_test() as pilot:
            app.run_turn('private prompt')
            await app.workers.wait_for_complete()
            await pilot.pause()
            if cancelled:
                app.notify_attention.assert_not_called()
            else:
                app.notify_attention.assert_called_once_with(Attention.FAILED)

    asyncio.run(scenario())


def test_should_deliver_from_worker_and_respect_opt_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import threading

    async def scenario() -> None:
        app = make_app(tmp_path)
        main_thread = threading.get_ident()
        delivered: list[tuple[Attention, int, NotificationContext]] = []
        app._session.title = 'Review alerts'
        monkeypatch.setattr(app, 'repository_label', lambda: 'sigil:feature')
        monkeypatch.setattr(SigilApp, 'is_headless', property(lambda _: False))
        monkeypatch.setattr(
            'src.ui.app.send_notification',
            lambda event, context: delivered.append((event, threading.get_ident(), context)) or True,
        )
        async with app.run_test():
            app.notify_attention(Attention.QUESTION)
            await app.workers.wait_for_complete()
            assert delivered[0][0] is Attention.QUESTION
            assert delivered[0][1] != main_thread
            assert delivered[0][2].title == 'sigil:feature — Review alerts'
            app._preferences = Preferences(desktop_notifications=False)
            app.notify_attention(Attention.FINISHED)
            await app.workers.wait_for_complete()
            assert len(delivered) == 1

    asyncio.run(scenario())


def test_should_keep_many_alerts_in_upper_screen(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = make_app(tmp_path)
        async with app.run_test(size=(80, 24), notifications=True) as pilot:
            prompt = app.query_one(PromptInput)
            for index in range(8):
                app.notify(f'Server {index} connected', timeout=30)
            await pilot.pause()
            rack = app.screen.query_one('ToastRack')
            assert rack.region.y == 0
            assert rack.region.bottom < prompt.region.y
            assert rack.region.height <= 10
            assert app.focused is prompt

    asyncio.run(scenario())
