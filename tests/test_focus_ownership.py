"""The keyboard has one owner at a time, and no click takes it away.

Every scroll surface in the app is a `VerticalScroll`, which is focusable by
default: a click on the transcript, on the side panel or on the debug view used
to move the focus there, and from then on everything typed went nowhere. The
question panel has the same problem from the other side — it is drawn with a
highlighted row that stops answering the arrows.

These tests hold the invariant: reading the interface never costs the keyboard,
scrolling still works, and the question panel keeps the keys (and `Esc`) while it
is open, wherever the pointer went.
"""

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual import events
from textual.widgets import OptionList

from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models.question_item import QuestionItem, QuestionOption
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.question_panel import QuestionPanel
from src.ui.widgets.status_bar import StatusBar

CTRL_LINES = 5
"""Lines the `ctrl+↑` / `ctrl+↓` pair moves: the step the panel already documents."""


def _app(tmp: str, hub: QuestionHub | None = None) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
        question_hub=hub,
    )


def _question(label: str = 'What do we do?') -> QuestionItem:
    return QuestionItem(
        question=label,
        options=[
            QuestionOption(label='Recommended', recommended=True),
            QuestionOption(label='Alternative'),
        ],
    )


async def _fill(pilot, app: SigilApp, rows: int = 30) -> ChatLog:
    """A transcript long enough to scroll."""
    chat = app.query_one(ChatLog)
    for index in range(rows):
        await chat.add_user(f'message {index}')
    await pilot.pause()
    return chat


async def _wheel_up(pilot, widget, times: int = 2) -> None:
    """Scrolls the wheel over a widget, the way a terminal delivers it."""
    region = widget.region
    await pilot._post_mouse_events(  # the pilot has no wheel helper of its own
        (events.MouseScrollUp,),
        offset=(region.x + 5, region.y + 5),
        times=times,
    )
    await pilot.pause()


def test_a_click_on_the_transcript_keeps_the_keys_in_the_composer():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await _fill(pilot, app)
                prompt = app.query_one(PromptInput)
                assert app.focused is prompt

                await pilot.click('#chat')
                await pilot.pause()
                assert app.focused is prompt

                await pilot.press('h', 'i')
                assert prompt.value == 'hi'

    asyncio.run(inner())


def test_a_click_on_the_side_panel_keeps_the_keys_in_the_composer():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await _fill(pilot, app)
                prompt = app.query_one(PromptInput)

                await pilot.click('#sidebar-scroll')
                await pilot.pause()
                assert app.focused is prompt

                await pilot.press('h')
                assert prompt.value == 'h'

    asyncio.run(inner())


def test_tab_stays_in_the_composer():
    """Nothing reachable by `Tab` is worth stranding the cursor for."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await _fill(pilot, app)
                prompt = app.query_one(PromptInput)

                await pilot.press('tab')
                await pilot.pause()
                assert app.focused is prompt

                await pilot.press('shift+tab')
                await pilot.pause()
                assert app.focused is prompt

    asyncio.run(inner())


def test_the_wheel_still_scrolls_the_transcript():
    """The regression the fix could cause: no focus, no scroll."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chat = await _fill(pilot, app)
                assert chat.is_vertical_scroll_end
                before = chat.scroll_offset.y

                await _wheel_up(pilot, chat)
                assert chat.scroll_offset.y < before
                assert app.focused is app.query_one(PromptInput)

    asyncio.run(inner())


def test_pageup_and_pagedown_scroll_the_transcript_from_the_composer():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chat = await _fill(pilot, app)
                prompt = app.query_one(PromptInput)
                assert chat.is_vertical_scroll_end

                await pilot.press('pageup')
                await pilot.pause()
                assert not chat.is_vertical_scroll_end
                assert app.focused is prompt

                raised = chat.scroll_offset.y
                await pilot.press('pagedown')
                await pilot.pause()
                assert chat.scroll_offset.y > raised

    asyncio.run(inner())


def test_ctrl_arrows_scroll_the_transcript_from_the_composer():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chat = await _fill(pilot, app)
                prompt = app.query_one(PromptInput)
                start = chat.scroll_offset.y

                await pilot.press('ctrl+up')
                await pilot.pause()
                assert chat.scroll_offset.y == start - CTRL_LINES
                assert app.focused is prompt

                await pilot.press('ctrl+down')
                await pilot.pause()
                assert chat.scroll_offset.y == start

    asyncio.run(inner())


def test_the_panel_keeps_the_keys_after_a_click_outside_it():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await _fill(pilot, app)
                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()

                panel = app.query_one(QuestionPanel)
                options = panel.query_one(OptionList)
                assert app.focused is options

                # Reading the conversation, or the panel's own title, is not a way
                # out of the question: the keys stay where the answer is.
                for selector in ('#chat', '#sidebar-scroll', '#question-title'):
                    await pilot.click(selector)
                    await pilot.pause()
                    assert app.focused is options

                await pilot.press('down')
                await pilot.pause()
                assert options.highlighted == 1
                await pilot.press('enter')
                answers = await asyncio.wait_for(task, timeout=5)
                assert answers is not None
                assert answers[0].answer == 'Alternative'

    asyncio.run(inner())


def test_the_panel_keeps_the_keys_in_the_free_text_field():
    """The same rule, one state further: a click does not close the field."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()

                panel = app.query_one(QuestionPanel)
                # The free-text row is the last one the list offers.
                await pilot.press('down', 'down', 'enter')
                await pilot.pause()
                field = panel.query_one('#question-custom')
                assert field.display is True
                assert app.focused is field

                await pilot.click('#chat')
                await pilot.pause()
                assert app.focused is field

                await pilot.press('m', 'i', 'n', 'e')
                await pilot.press('enter')
                answers = await asyncio.wait_for(task, timeout=5)
                assert answers is not None
                assert answers[0].answer == 'mine'

    asyncio.run(inner())


def test_escape_outside_the_panel_cancels_the_question_not_the_turn():
    """`Esc` means the same thing everywhere while a question is open.

    The panel is not in the focus chain here — nothing is — which is the state a
    widget that just went away leaves behind. Falling through to the abort path
    would kill the run and leave the question on screen with nobody to answer it.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                # A question only shows up mid-turn: that is when the abort exists.
                app._turn_active = True
                try:
                    task = asyncio.create_task(app.ask_question([_question()]))
                    await pilot.pause()
                    await pilot.pause()
                    app.set_focus(None)
                    await pilot.pause()

                    await pilot.press('escape')
                    assert await asyncio.wait_for(task, timeout=5) is None
                    await pilot.pause()

                    assert app.query_one(QuestionPanel).display is False
                    assert app._abort_deadline is None
                    assert 'again' not in app.query_one(StatusBar).message
                finally:
                    app._turn_active = False

    asyncio.run(inner())


def test_the_composer_border_follows_the_focus():
    """A composer that is not listening has to look like one."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                prompt = app.query_one(PromptInput)
                focused = prompt.styles.border_top

                prompt.blur()
                await pilot.pause()
                blurred = prompt.styles.border_top
                assert app.focused is not prompt
                assert focused != blurred

                prompt.disabled = True
                await pilot.pause()
                disabled = prompt.styles.border_top
                opacity = prompt.styles.text_opacity
                assert disabled != focused
                assert opacity is not None and float(opacity) < 1

    asyncio.run(inner())
