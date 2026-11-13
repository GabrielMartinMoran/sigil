"""The Sigil mark of an empty session, and when it must disappear.

The mark is not a widget: the transcript paints it behind its children until the
first block arrives. These tests cover the two halves that can break independently
— the drawing at a size that does not fit it, and the transcript deciding whether
there is anything to draw.
"""

import asyncio

from pydantic_ai.messages import ModelRequest, UserPromptPart
from textual.app import App, ComposeResult
from textual.theme import Theme

from src.engine.themes import ThemeRegistry
from src.ui.widgets.chat_log import ChatLog, SubagentReport, UserMessage
from src.ui.widgets.empty_logo import LOGO, SMALL_LOGO, logo_lines

INK = next(glyph for glyph in LOGO[0] if glyph != ' ')
"""The cell the drawings are made of, so a redrawn mark does not break these tests."""


class Harness(App):
    def compose(self) -> ComposeResult:
        yield ChatLog(id='chat', welcome=True)


class SubagentHarness(App):
    def compose(self) -> ComposeResult:
        yield ChatLog(id='agent-chat')


def with_themes(app: App, theme: str = 'sigil') -> App:
    for definition in ThemeRegistry.load().themes.values():
        app.register_theme(definition.to_textual())
    app.theme = theme
    return app


def test_the_drawing_is_scaled_to_the_space_it_is_given():
    rows = logo_lines(30, 16, '#ff9d00', '#dc3c00')
    assert len(rows) <= 16
    assert max(len(row.plain) for row in rows) <= 30
    assert INK in ''.join(row.plain for row in rows)
    assert len({row.style for row in rows}) > 1


def test_the_largest_variant_that_fits_whole_is_used():
    large = logo_lines(100, 60, '#ff9d00', '#dc3c00')
    small = logo_lines(45, 40, '#ff9d00', '#dc3c00')
    assert [row.plain for row in large] == [row.rstrip() for row in LOGO]
    assert [row.plain for row in small] == [row.rstrip() for row in SMALL_LOGO]


def test_a_theme_with_one_color_still_gets_a_gradient():
    """A flat silhouette would lose the depth the mark is drawn for."""

    async def run() -> None:
        app = Harness()
        app.register_theme(Theme(name='monochrome', primary='#ffffff', secondary='#ffffff', foreground='#ffffff'))
        app.theme = 'monochrome'
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            assert len({span.style for span in app.query_one(ChatLog).render().spans}) > 1

    asyncio.run(run())


def test_an_empty_transcript_shows_the_mark_and_a_started_one_does_not():
    async def run() -> None:
        app = with_themes(Harness())
        async with app.run_test(size=(80, 30)) as pilot:
            chat = app.query_one(ChatLog)
            await chat.add_notice('New session 1a2b3c.')
            await pilot.pause()
            # A notice is chrome, not conversation: a new session is still empty.
            assert INK in app.export_screenshot()
            sigil = app.export_screenshot()
            app.theme = 'dark'
            chat.repaint()
            await pilot.pause()
            assert app.export_screenshot() != sigil

            await chat.add_user('hello')
            await pilot.pause()
            assert INK not in app.export_screenshot()

            await chat.clear()
            await chat.render_history([ModelRequest(parts=[UserPromptPart(content='restored')])])
            await pilot.pause()
            assert INK not in app.export_screenshot()

            await chat.clear()
            await pilot.pause()
            assert INK in app.export_screenshot()

    asyncio.run(run())


def test_a_subagent_transcript_never_shows_the_mark():
    """The mark belongs to the app's own conversation, not to every ChatLog."""

    async def run() -> None:
        app = with_themes(SubagentHarness())
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            assert INK not in app.export_screenshot()

    asyncio.run(run())


def test_repainting_an_empty_session_from_the_service_brings_the_mark_back():
    """The attached terminal repaints from rows; an empty transcript is not a bug.

    `clear()` is what a rebuild starts with, and it puts the mark back because a
    cleared log looks exactly like a session with nothing in it. Repainting nothing
    on top of it must leave that alone — otherwise the one case the mark exists for,
    a session whose first message has not been written, is the case that loses it.
    """

    async def run() -> None:
        app = with_themes(Harness())
        async with app.run_test(size=(80, 30)) as pilot:
            chat = app.query_one(ChatLog)
            await chat.render_history([ModelRequest(parts=[UserPromptPart(content='something')])])
            await pilot.pause()
            assert INK not in app.export_screenshot()

            # The client's repaint path: clear, then paint the service's rows.
            await chat.clear()
            await chat.render_items([])
            await pilot.pause()
            assert INK in app.export_screenshot()

            # And rows that do carry something retire the mark again, so the repaint
            # does not leave the drawing behind a restored conversation.
            await chat.render_items([{'kind': 'user', 'text': 'restored from the service'}])
            await pilot.pause()
            assert INK not in app.export_screenshot()

    asyncio.run(run())


def test_a_report_from_the_service_is_painted_as_a_report():
    """A projected report is a card, not a line the reader appears to have typed.

    The service persists a subagent's answer as a durable user input, so the item it
    sends carries the envelope's facts instead of the envelope: the client paints that
    as the report it is. A user line would put the squad's words in the reader's mouth.
    """

    async def run() -> None:
        app = with_themes(Harness())
        async with app.run_test(size=(80, 30)) as pilot:
            chat = app.query_one(ChatLog)
            await chat.render_items(
                [
                    {
                        'kind': 'subagent_report',
                        'index': 0,
                        'node_id': 'n1',
                        'label': 'Scout',
                        'short_id': 'n1sh',
                        'text': 'found it',
                    }
                ]
            )
            await pilot.pause()
            reports = list(chat.query(SubagentReport))
            assert len(reports) == 1
            assert reports[0].node_id == 'n1'
            assert not list(chat.query(UserMessage)), 'a report is never painted as a user message'

    asyncio.run(run())
