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

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual import events
from textual.pilot import Pilot
from textual.widgets import OptionList, Static

from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models.question_item import QuestionItem, QuestionOption
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.question_panel import QuestionPanel
from src.ui.widgets.sidebar_panel import SidebarPanel
from src.ui.widgets.status_bar import StatusBar
from tests.ui_helpers import fill_chat

pytestmark = pytest.mark.usefixtures('memory_databases')

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


async def _fill(pilot: Pilot, app: SigilApp) -> ChatLog:
    """A transcript long enough to scroll."""
    chat = app.query_one(ChatLog)
    await fill_chat(chat)
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
                await _fill(pilot, app)
                prompt = app.query_one(PromptInput)

                await pilot.press('tab')
                assert app.focused is prompt

                await pilot.press('shift+tab')
                assert app.focused is prompt

    asyncio.run(inner())


def test_the_wheel_still_scrolls_the_transcript():
    """The regression the fix could cause: no focus, no scroll."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 40)) as pilot:
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
                chat = await _fill(pilot, app)
                prompt = app.query_one(PromptInput)
                assert chat.is_vertical_scroll_end

                await pilot.press('pageup')
                assert not chat.is_vertical_scroll_end
                assert app.focused is prompt

                raised = chat.scroll_offset.y
                await pilot.press('pagedown')
                assert chat.scroll_offset.y > raised

    asyncio.run(inner())


def test_ctrl_arrows_scroll_the_transcript_from_the_composer():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 40)) as pilot:
                chat = await _fill(pilot, app)
                prompt = app.query_one(PromptInput)
                start = chat.scroll_offset.y

                await pilot.press('ctrl+up')
                assert chat.scroll_offset.y == start - CTRL_LINES
                assert app.focused is prompt

                await pilot.press('ctrl+down')
                assert chat.scroll_offset.y == start

    asyncio.run(inner())


def test_every_option_is_one_line_at_any_width():
    """A wrapped row makes the list read as two lists, so the row is clipped.

    `OptionList` renders each option through its own `Visual` at the list width, so
    a `no_wrap` on the `Text` is discarded and the row wraps anyway. The panel cuts
    the row to the width the list actually has. This checks the render, not the
    attribute: what matters is that the painted strip is one line.

    Also pinned: the clipped part is the description. The label and the
    `[recommended]` tag survive, because they are what the decision is made on.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _app(tmp, hub)
            async with app.run_test(size=(60, 20)) as pilot:
                question = QuestionItem(
                    question='Which one?',
                    options=[
                        QuestionOption(
                            label='Finish the parser refactor first',
                            description='Touches parser.py only and leaves the writer for later.',
                            recommended=True,
                        ),
                        QuestionOption(
                            label='Close the writer coverage first',
                            description='Three tests, and the refactor waits its turn.',
                        ),
                    ],
                )
                task = asyncio.create_task(app.ask_question([question]))
                await pilot.pause()
                await pilot.pause()

                options = app.query_one(QuestionPanel).query_one(OptionList)
                for index in range(options.option_count):
                    prompt = options.get_option_at_index(index).prompt
                    assert '\n' not in prompt.plain
                    # Clipped to the list's own width, never past it.
                    assert prompt.cell_len <= options.scrollable_content_region.width

                first = options.get_option_at_index(0).prompt.plain
                assert 'Finish the parser refactor first' in first
                assert '[recommended]' in first
                assert '…' in first

                await pilot.press('escape')
                await asyncio.wait_for(task, timeout=5)

    asyncio.run(inner())


def test_the_help_line_never_scrolls_away():
    """It is the only legend while the composer is out of the way."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _app(tmp, hub)
            async with app.run_test(size=(60, 18)) as pilot:
                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()

                panel = app.query_one(QuestionPanel)
                help_line = panel.query_one('#question-help', Static)
                # Docked, so it is on screen and at the bottom of the panel.
                assert help_line.display is True
                assert help_line.region.height == 2  # the padding and the line
                assert help_line.region.bottom <= panel.region.bottom
                # And the options are still on screen: the legend does not win rows
                # from the choices.
                options = panel.query_one(OptionList)
                assert options.region.height >= 2

                await pilot.press('escape')
                await asyncio.wait_for(task, timeout=5)

    asyncio.run(inner())


def test_the_composer_steps_aside_for_the_question():
    """The box that owns the keyboard is the box on screen.

    A composer that is visible and cannot be typed into is a lie, and it was the
    most prominent thing on the screen. While a question is open the composer is
    gone and the question takes its place, across the full width.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                prompt = app.query_one(PromptInput)
                prompt.value = 'half a thought'
                await pilot.pause()

                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()

                panel = app.query_one(QuestionPanel)
                assert prompt.display is False
                assert panel.display is True
                # Full width, and right where the composer was.
                assert panel.region.width == app.screen.size.width
                assert panel.region.y < app.query_one(StatusBar).region.y

                await pilot.press('enter')
                await asyncio.wait_for(task, timeout=5)
                await pilot.pause()

                # The composer is back with what was typed, and with the keys.
                assert prompt.display is True
                assert prompt.value == 'half a thought'
                assert app.focused is prompt

    asyncio.run(inner())


def test_a_question_leaves_the_side_panel_preference_alone():
    """`/sidebar off` is the user's decision, and a question is not a reason to redo it."""

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                sidebar = app.query_one(SidebarPanel)
                sidebar.set_shown(False)
                await pilot.pause()

                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()
                assert sidebar.display is False

                await pilot.press('enter')
                await asyncio.wait_for(task, timeout=5)
                await pilot.pause()
                assert sidebar.display is False
                assert sidebar.shown is False

    asyncio.run(inner())


def test_the_side_panel_shrinks_with_the_transcript_instead_of_stepping_out():
    """Both columns give up height; neither gives up its place.

    The panel is a sibling of `#main`, so it takes its height from the whole row above
    it and the two columns shrink together. The side panel used to be hidden here,
    which was a misreading of the request: it has its own scroll, so a shorter panel is
    still a usable one — and its roadmap is often what the question is about.
    """

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _app(tmp, hub)
            async with app.run_test(size=(120, 40)) as pilot:
                chat = await _fill(pilot, app)
                sidebar = app.query_one(SidebarPanel)
                chat_height = chat.size.height
                sidebar_height = sidebar.size.height
                width = chat.size.width

                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()

                # Still there, same column, same width.
                assert sidebar.display is True
                assert chat.size.width == width
                # And both lost height to the question, in step.
                assert sidebar.size.height < sidebar_height
                assert chat.size.height < chat_height
                assert sidebar.size.height == sidebar_height - (chat_height - chat.size.height)

                await pilot.press('escape')
                await asyncio.wait_for(task, timeout=5)
                await pilot.pause()
                assert sidebar.size.height == sidebar_height
                assert chat.size.height == chat_height

    asyncio.run(inner())


def test_the_panel_keeps_the_keys_after_a_click_outside_it():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await _fill(pilot, app)
                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()

                panel = app.query_one(QuestionPanel)
                options = panel.query_one(OptionList)
                assert app.focused is options

                # Reading the conversation, or the panel's own frame, is not a way
                # out of the question: the keys stay where the answer is.
                for selector in ('#chat', '#question-title', '#question-progress'):
                    await pilot.click(selector)
                    await pilot.pause()
                    assert app.focused is options

                await pilot.press('down')
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
                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()

                panel = app.query_one(QuestionPanel)
                # The free-text row is the last one the list offers.
                await pilot.press('down', 'down', 'enter')
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


def test_assistant_markdown_can_be_selected():
    from textual.selection import SELECT_ALL

    from src.ui.widgets.chat_log import AssistantBlock

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await chat.begin_response()
                await chat.add_text('Hello **world**')
                chat.finalize()
                await pilot.pause()
                block = chat.query_one(AssistantBlock)
                selected = block.get_selection(SELECT_ALL)
                assert selected is not None
                assert 'Hello world' in selected[0]

    asyncio.run(inner())


def test_assistant_markdown_link_opens_in_browser():
    from src.ui.widgets.chat_log import AssistantBlock

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            opened = []
            app.open_url = lambda url, **kwargs: opened.append(url)
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await chat.begin_response()
                await chat.add_text('[example](https://example.com)')
                chat.finalize()
                await pilot.pause()
                block = chat.query_one(AssistantBlock)
                await pilot.click(block, offset=(1, 0))
                assert opened == ['https://example.com']

    asyncio.run(inner())


def test_assistant_plain_url_opens_in_browser():
    from src.ui.widgets.chat_log import AssistantBlock

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            opened = []
            app.open_url = lambda url, **kwargs: opened.append(url)
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await chat.begin_response()
                await chat.add_text('https://example.com')
                chat.finalize()
                await pilot.pause()
                await pilot.click(chat.query_one(AssistantBlock), offset=(1, 0))
                assert opened == ['https://example.com']

    asyncio.run(inner())


def test_error_notice_is_also_shown_as_a_toast():
    from src.ui.messages import ChatNotice

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            toasts = []
            app.notify = lambda message, **kwargs: toasts.append((message, kwargs))
            async with app.run_test() as pilot:
                app.post_message(ChatNotice('Provider failed', style='bold red'))
                await pilot.pause()
                assert toasts == [('Provider failed', {'severity': 'error'})]

    asyncio.run(inner())


def test_url_in_notice_opens_in_browser():
    from src.ui.widgets.chat_log import LinkNotice

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            app = _app(tmp)
            opened = []
            app.open_url = lambda url, **kwargs: opened.append(url)
            async with app.run_test(size=(100, 40)) as pilot:
                chat = app.query_one(ChatLog)
                await chat.add_notice('https://example.com')
                await pilot.pause()
                await pilot.click(chat.query_one(LinkNotice), offset=(1, 0))
                assert opened == ['https://example.com']

    asyncio.run(inner())
