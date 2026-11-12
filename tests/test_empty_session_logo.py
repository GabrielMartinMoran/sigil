import asyncio

from pydantic_ai.messages import ModelRequest, UserPromptPart
from textual.app import App, ComposeResult

from src.engine.themes import ThemeRegistry
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.empty_logo import LOGO, SMALL_LOGO, logo_text


class Harness(App):
    def compose(self) -> ComposeResult:
        yield ChatLog(id='chat')


def test_logo_fits_and_has_a_gradient():
    logo = logo_text(30, 16, '#ff9d00', '#dc3c00')
    lines = logo.plain.splitlines()
    assert len(lines) <= 16
    assert max(map(len, lines)) <= 30
    assert '▓' in logo.plain
    assert len({span.style for span in logo.spans}) > 1


def test_logo_chooses_the_largest_variant_that_fits():
    large = logo_text(100, 60, '#ff9d00', '#dc3c00')
    small = logo_text(40, 30, '#ff9d00', '#dc3c00')
    assert large.plain.splitlines()[0].rstrip() == LOGO[0]
    assert len(large.plain.splitlines()) == len(LOGO)
    assert small.plain.splitlines()[0].rstrip() == SMALL_LOGO[0]
    assert len(small.plain.splitlines()) == len(SMALL_LOGO)


def test_logo_keeps_a_gradient_for_a_monochrome_theme():
    async def run() -> None:
        from textual.theme import Theme

        app = Harness()
        app.register_theme(Theme(name='monochrome', primary='#ffffff', secondary='#ffffff', foreground='#ffffff'))
        app.theme = 'monochrome'
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            logo = app.query_one(ChatLog).render()
            assert len({span.style for span in logo.spans}) > 1

    asyncio.run(run())


def test_empty_session_logo_tracks_messages_history_and_theme():
    async def run() -> None:
        app = Harness()
        for theme in ThemeRegistry.load().themes.values():
            app.register_theme(theme.to_textual())
        app.theme = 'sigil'
        async with app.run_test(size=(80, 30)) as pilot:
            chat = app.query_one(ChatLog)
            await chat.add_notice('New session')
            await pilot.pause()
            assert '▓' in app.export_screenshot()
            before = app.export_screenshot()
            app.theme = 'dark'
            chat.repaint()
            await pilot.pause()
            assert app.export_screenshot() != before
            await chat.add_user('hello')
            await pilot.pause()
            assert '▓' not in app.export_screenshot()
            await chat.clear()
            await chat.render_history([ModelRequest(parts=[UserPromptPart(content='restored')])])
            await pilot.pause()
            assert '▓' not in app.export_screenshot()
            await chat.clear()
            await pilot.pause()
            assert '▓' in app.export_screenshot()

    asyncio.run(run())
