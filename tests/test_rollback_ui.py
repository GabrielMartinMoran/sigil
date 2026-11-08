"""The screen, the command and the click.

The rules here are the ones that make the gesture safe: a click opens the *choice* and
never the destruction, the fork leaves the original alone, and a rollback rebuilds the
conversation on screen from the rows that survived.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.test import TestModel

from src.engine.agents.rollback import Rollback
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog, UserMessage
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.rollback_screen import (
    ROLLBACK_ONLY,
    Point,
    RollbackChoice,
    RollbackScreen,
    action_option,
    point_option,
)
from tests.ui_helpers import render_text


def _turn(index: int) -> list:
    return [
        ModelRequest(parts=[UserPromptPart(content=f'question {index}')], run_id=f'r{index}'),
        ModelResponse(parts=[TextPart(content=f'answer {index}')], run_id=f'r{index}'),
    ]


def _app(tmp: str, *, turns: int = 2) -> SigilApp:
    store = SessionStore(cwd=Path(tmp) / 'ws', base_dir=Path(tmp) / 'sessions')
    session = store.create_session()
    session.messages = [message for index in range(turns) for message in _turn(index)]
    store.save(session)
    rollback = Rollback(store, workspace=Path(tmp) / 'ws')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=session,
        rollback=rollback,
    )


# --- the screen ---------------------------------------------------------------


def test_a_point_shows_the_moment_the_prompt_and_the_file_cost():
    from src.engine.agents.rollback import Checkpoint

    point = Point(
        checkpoint=Checkpoint(
            id='c1',
            session_id='s1',
            execution_id='e1',
            turn_id='t1',
            at='2026-11-06T12:34:56+00:00',
            reason='prompt',
            label='',
            tree_commit=None,
        ),
        preview='fix the parser',
        files=2,
    )

    text = str(point_option(point, index=3))

    assert '12:34:56' in text
    assert 'fix the parser' in text
    assert '2 files' in text


def test_a_point_with_no_files_says_so_instead_of_nothing():
    from src.engine.agents.rollback import Checkpoint

    point = Point(
        checkpoint=Checkpoint(
            id='c1',
            session_id='s1',
            execution_id='e1',
            turn_id='t1',
            at='2026-11-06T00:00:00+00:00',
            reason='prompt',
            label='',
            tree_commit=None,
        ),
        preview='',
        files=0,
    )
    text = str(point_option(point, index=1))
    assert 'no files' in text


def test_the_destructive_action_is_the_one_marked_dangerous():
    """The eye has to catch which of the three can destroy a turn."""
    dangerous = action_option('rollback_files', 'Roll back, files included', 'x')
    safe = action_option('fork', 'Fork here', 'x')

    assert any(span.style == 'bold red' for span in dangerous.prompt.spans)
    assert not any(span.style == 'bold red' for span in safe.prompt.spans)


# --- the command ---------------------------------------------------------------


def test_the_command_forks_and_leaves_the_session_on_screen(monkeypatch):
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            original = app._session.id
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app.run_worker(app.handle_command('/rollback'))
                for _ in range(20):
                    await pilot.pause()
                    if isinstance(app.screen, RollbackScreen):
                        break
                assert isinstance(app.screen, RollbackScreen)

                # Two stages: the point, then the action.
                await pilot.press('enter')
                await pilot.pause()
                # `Fork here` is the third action.
                await pilot.press('down', 'down', 'enter')
                for _ in range(20):
                    await pilot.pause()
                    if not isinstance(app.screen, RollbackScreen):
                        break

                assert app._session.id == original
                assert len(app._store.list_sessions()) == 2

    asyncio.run(inner())


def test_the_command_rolls_back_and_rebuilds_the_conversation(monkeypatch):
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, turns=2)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                before = len(app._session.messages)
                app.run_worker(app.handle_command('/rollback'))
                for _ in range(20):
                    await pilot.pause()
                    if isinstance(app.screen, RollbackScreen):
                        break

                await pilot.press('enter')
                await pilot.pause()
                await pilot.press('enter')  # the first action: roll back with files
                for _ in range(30):
                    await pilot.pause()
                    if not isinstance(app.screen, RollbackScreen):
                        break

                # The newest point is the second turn: rolling back to it keeps the first.
                assert len(app._session.messages) < before
                chat = app.query_one(ChatLog)
                assert len(app._session.messages) == 2
                assert chat is not None

    asyncio.run(inner())


# --- the prompt comes back -----------------------------------------------------


def test_a_rollback_puts_the_prompt_back_in_the_composer():
    """Going back to a message is "let me say it differently", so it is ready to send."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, turns=2)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                point = app._rollback.points(app._session.id)[0]
                chat = app.query_one(ChatLog)

                await app.apply_rollback(chat, RollbackChoice(turn_id=point.turn_id, action=ROLLBACK_ONLY))

                prompt = app.query_one(PromptInput)
                assert prompt.text.strip() == 'question 1'
                # Ready to be edited and sent, not a dead line in the transcript.
                assert prompt.has_focus

    asyncio.run(inner())


def test_a_rollback_keeps_the_draft_that_was_in_the_composer():
    """The prompt comes back; what the user was already typing does not disappear."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, turns=2)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                prompt = app.query_one(PromptInput)
                prompt.text = 'a draft I was writing'
                point = app._rollback.points(app._session.id)[0]
                chat = app.query_one(ChatLog)

                await app.apply_rollback(chat, RollbackChoice(turn_id=point.turn_id, action=ROLLBACK_ONLY))

                lines = prompt.text.splitlines()
                assert 'a draft I was writing' in lines
                # The restored prompt is the last line: it is the one Enter sends.
                assert lines[-1].strip() == 'question 1'

    asyncio.run(inner())


def test_a_prompt_that_is_only_a_screenshot_gets_its_marker_back():
    """An image sent without words has no `[Image #n]`, and the composer needs one."""

    async def inner():
        import io

        from PIL import Image
        from pydantic_ai.messages import BinaryImage

        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), 'blue').save(buffer, format='PNG')
        payload = buffer.getvalue()

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=Path(tmp) / 'ws', base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            session.messages = [
                ModelRequest(parts=[UserPromptPart(content='question 0')], run_id='r0'),
                ModelResponse(parts=[TextPart(content='answer 0')], run_id='r0'),
                ModelRequest(
                    parts=[
                        UserPromptPart(content=[BinaryImage(data=payload, media_type='image/png', identifier='img_z')])
                    ],
                    run_id='r1',
                ),
                ModelResponse(parts=[TextPart(content='answer 1')], run_id='r1'),
            ]
            store.save(session)
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=session,
                rollback=Rollback(store, workspace=Path(tmp) / 'ws'),
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                point = app._rollback.points(session.id)[0]
                chat = app.query_one(ChatLog)

                await app.apply_rollback(chat, RollbackChoice(turn_id=point.turn_id, action=ROLLBACK_ONLY))

                prompt = app.query_one(PromptInput)
                assert prompt.text.strip() == '[Image #1]'
                assert len(prompt.attachments) == 1
                assert prompt.attachments[0].image_id == 'img_z'

    asyncio.run(inner())


def test_a_rollback_brings_the_images_back_with_their_prompt():
    """A message whose text came back without its picture points at nothing."""

    async def inner():
        import io

        from PIL import Image
        from pydantic_ai.messages import BinaryImage

        buffer = io.BytesIO()
        Image.new('RGB', (8, 8), 'red').save(buffer, format='PNG')
        payload = buffer.getvalue()

        with tempfile.TemporaryDirectory() as tmp:
            store = SessionStore(cwd=Path(tmp) / 'ws', base_dir=Path(tmp) / 'sessions')
            session = store.create_session()
            session.messages = [
                ModelRequest(parts=[UserPromptPart(content='question 0')], run_id='r0'),
                ModelResponse(parts=[TextPart(content='answer 0')], run_id='r0'),
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=[
                                'look at [Image #1]',
                                BinaryImage(data=payload, media_type='image/png', identifier='img_abc12345'),
                            ]
                        )
                    ],
                    run_id='r1',
                ),
                ModelResponse(parts=[TextPart(content='answer 1')], run_id='r1'),
            ]
            store.save(session)
            app = SigilApp(
                agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
                store=store,
                session=session,
                rollback=Rollback(store, workspace=Path(tmp) / 'ws'),
            )
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                point = app._rollback.points(session.id)[0]
                chat = app.query_one(ChatLog)

                await app.apply_rollback(chat, RollbackChoice(turn_id=point.turn_id, action=ROLLBACK_ONLY))

                prompt = app.query_one(PromptInput)
                assert prompt.text.strip() == 'look at [Image #1]'
                attachments = prompt.attachments
                assert len(attachments) == 1
                assert attachments[0].data == payload
                assert attachments[0].media_type == 'image/png'
                assert attachments[0].image_id == 'img_abc12345'

    asyncio.run(inner())


def test_the_command_is_refused_while_a_turn_runs(monkeypatch):
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                app._turn_active = True
                chat = app.query_one(ChatLog)
                await app.show_rollback(chat)
                assert not isinstance(app.screen, RollbackScreen)
                assert any('A turn is running' in render_text(child.render()) for child in chat.children)

    asyncio.run(inner())


def test_a_numbered_argument_skips_the_list(monkeypatch):
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, turns=3)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # `handle_command` takes the name and the arguments apart: the prompt is
                # what splits the line, not the handler.
                app.run_worker(app.handle_command('/rollback', '2'))
                for _ in range(20):
                    await pilot.pause()
                    if isinstance(app.screen, RollbackScreen):
                        break
                screen = app.screen
                assert isinstance(screen, RollbackScreen)
                # Already on the actions of the second point, not on the list.
                assert screen._stage == 'actions'

    asyncio.run(inner())


# --- the click -----------------------------------------------------------------


def test_a_rendered_user_message_carries_its_position():
    """The position in the transcript is the seq, which is what maps it to a turn."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, turns=2)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                await chat.clear()
                await chat.render_history(app._session.messages)

                messages = [child for child in chat.children if isinstance(child, UserMessage)]
                assert [widget.seq for widget in messages] == [0, 2]

    asyncio.run(inner())


def test_clicking_a_message_opens_the_choice_on_that_point():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp, turns=2)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                first = next(child for child in chat.children if isinstance(child, UserMessage))

                first.on_click()  # what a click does
                for _ in range(20):
                    await pilot.pause()
                    if isinstance(app.screen, RollbackScreen):
                        break

                screen = app.screen
                assert isinstance(screen, RollbackScreen)
                # The stage is still the list: a click chooses a point, never an action.
                assert screen._stage == 'points'

    asyncio.run(inner())


def test_clicking_a_message_that_never_reached_a_checkpoint_does_nothing():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                # A line that has not reached a checkpoint has no position, so a click
                # does nothing: guessing one would open the wrong turn.
                widget = await app.query_one(ChatLog).add_user('not saved yet')
                assert isinstance(widget, UserMessage)
                assert widget.seq is None
                widget.on_click()
                for _ in range(5):
                    await pilot.pause()
                assert not isinstance(app.screen, RollbackScreen)

    asyncio.run(inner())


def test_rollback_modal_is_centered():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                app.push_screen(RollbackScreen([]))
                await pilot.pause()
                box = app.screen.query_one('#provider-box')
                assert abs(box.region.center[0] - app.screen.region.center[0]) <= 1
                assert abs(box.region.center[1] - app.screen.region.center[1]) <= 1

    asyncio.run(inner())


def test_rollback_restores_visible_assistant_answers():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                point = app._rollback.points(app._session.id)[0]
                chat = app.query_one(ChatLog)
                await app.apply_rollback(chat, RollbackChoice(turn_id=point.turn_id, action=ROLLBACK_ONLY))
                await pilot.pause()
                from src.ui.widgets.chat_log import AssistantBlock

                blocks = list(chat.query(AssistantBlock))
                assert len(blocks) == 1
                assert 'answer 0' in render_text(blocks[0]._build())

    asyncio.run(inner())
