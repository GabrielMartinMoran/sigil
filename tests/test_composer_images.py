"""Pasting an image into the composer, and deleting it as one block.

These are the behaviours the feature is judged by, so they are tested through the
real widget in a real app:

- `ctrl+v` with an image on the clipboard attaches it and writes `[Image #1]`
  where the cursor was.
- The marker is painted differently from the text around it.
- One `backspace` removes the whole marker, and the image goes with it — the send
  must not carry a picture the message no longer mentions.
- The numbers stay contiguous: delete the first of two and the second becomes the
  first, in the text and in the attachments.
- A clipboard with text and no image still pastes the text.
"""

from __future__ import annotations

import asyncio
import io
from typing import Self

from PIL import Image
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

import src.ui.widgets.prompt_input as prompt_module
from src.engine.attachments.clipboard import ClipboardUnavailable
from src.engine.attachments.image_attachment import ImageAttachment
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.turn_mixin import Steer
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from tests.ui_helpers import render_text


def _png(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color='#3465a4').save(buffer, format='PNG')
    return buffer.getvalue()


def _app(tmp_path) -> SigilApp:
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
    )


def _attachment(number: int) -> ImageAttachment:
    return ImageAttachment(number=number, data=_png(), media_type='image/png', width=40, height=30)


def _composer(app: SigilApp) -> PromptInput:
    return app.query_one(PromptInput)


class Clipboard:
    """Stands in for the desktop: `ctrl+v` answers with whatever this holds."""

    def __init__(self, image: bytes | None = None, *, error: str | None = None) -> None:
        self.image = image
        self.error = error

    def __enter__(self) -> Self:
        self._original = prompt_module.read_clipboard_image
        if self.error is not None:

            def failing(**_):
                raise ClipboardUnavailable(self.error)

            prompt_module.read_clipboard_image = failing
        else:
            prompt_module.read_clipboard_image = lambda **_: self.image
        return self

    def __exit__(self, *exc) -> None:
        prompt_module.read_clipboard_image = self._original


# --- attaching --------------------------------------------------------------


def test_ctrl_v_with_an_image_writes_the_marker_where_the_cursor_is(tmp_path):
    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)
            await pilot.press('m', 'i', 'r', 'a', 'space')
            with Clipboard(_png()):
                await pilot.press('ctrl+v')
                await pilot.pause()

            assert composer.text == 'mira [Image #1]'
            assert len(composer.attachments) == 1
            assert composer.attachments[0].media_type == 'image/png'

    asyncio.run(inner())


def test_the_marker_is_painted_differently_from_the_text_around_it(tmp_path):
    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)
            with Clipboard(_png()):
                await pilot.press('ctrl+v')
                await pilot.pause()
            await pilot.pause()

            # The style comes from the rendered strip: reverse video is what makes
            # the marker read as one block instead of nine characters.
            strip = composer.render_line(0)
            marker = [segment for segment in strip._segments if 'Image' in segment.text]
            assert marker, [segment.text for segment in strip._segments]
            assert marker[0].style is not None and marker[0].style.reverse

    asyncio.run(inner())


def test_two_pastes_are_numbered_in_order(tmp_path):
    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)
            with Clipboard(_png(10, 10)):
                await pilot.press('ctrl+v')
                await pilot.pause()
            with Clipboard(_png(20, 20)):
                await pilot.press('ctrl+v')
                await pilot.pause()

            assert composer.text == '[Image #1][Image #2]'
            assert [image.number for image in composer.attachments] == [1, 2]
            assert composer.attachments[1].width == 20

    asyncio.run(inner())


def test_a_clipboard_without_an_image_still_pastes_text(tmp_path):
    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)
            # No image: the widget falls back to the text paste it always had.
            app.copy_to_clipboard('texto del portapapeles')
            with Clipboard(None):
                await pilot.press('ctrl+v')
                await pilot.pause()

            assert composer.text == 'texto del portapapeles'
            assert composer.attachments == ()

    asyncio.run(inner())


def test_a_missing_clipboard_tool_is_reported_once_and_text_still_pastes(tmp_path):
    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)
            app.copy_to_clipboard('texto')
            with Clipboard(error='Install `xclip` to paste screenshots.'):
                await pilot.press('ctrl+v')
                await pilot.pause()

            assert composer.text == 'texto'
            notices = [render_text(child.render()) for child in app.query_one(ChatLog).children]
            assert any('xclip' in notice for notice in notices)

    asyncio.run(inner())


# --- deleting it ------------------------------------------------------------


def test_one_backspace_removes_the_whole_marker_and_the_image(tmp_path):
    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)
            with Clipboard(_png()):
                await pilot.press('ctrl+v')
                await pilot.pause()

            await pilot.press('backspace')
            await pilot.pause()

            assert composer.text == ''
            assert composer.attachments == ()

    asyncio.run(inner())


def test_backspace_inside_prose_still_deletes_one_character(tmp_path):
    """Taking the key away from the text area everywhere would be a regression."""

    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)
            await pilot.press('h', 'o', 'l', 'a')

            await pilot.press('backspace')
            await pilot.pause()

            assert composer.text == 'hol'

    asyncio.run(inner())


def test_deleting_the_first_of_two_images_renumbers_the_second(tmp_path):
    """`[Image #2]` on its own points at a picture that is no longer attached."""

    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)
            with Clipboard(_png(10, 10)):
                await pilot.press('ctrl+v')
                await pilot.pause()
            await pilot.press('space')
            with Clipboard(_png(20, 20)):
                await pilot.press('ctrl+v')
                await pilot.pause()

            # The cursor sits at the end; the first marker is deleted from the start.
            composer.move_cursor((0, 0))
            await pilot.press('delete')
            await pilot.pause()

            assert composer.text == ' [Image #1]'
            assert [image.number for image in composer.attachments] == [1]
            assert composer.attachments[0].width == 20

    asyncio.run(inner())


def test_clearing_the_composer_drops_the_attachments(tmp_path):
    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)
            with Clipboard(_png()):
                await pilot.press('ctrl+v')
                await pilot.pause()

            composer.clear()

            assert composer.attachments == ()
            # The next paste starts at one again.
            with Clipboard(_png()):
                await pilot.press('ctrl+v')
                await pilot.pause()
            assert composer.text == '[Image #1]'

    asyncio.run(inner())


def test_a_message_that_missed_the_seam_comes_back_with_its_images(tmp_path):
    """Text without its picture would point at an `[Image #1]` that is gone.

    The turn hands the leftovers back with the exact expression checked here; a
    race that loses the last seam does not happen on demand, so what is pinned is
    that the hand-back keeps the images.
    """

    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)
            leftovers = [Steer(text='volvé [Image #1]', attachments=(_attachment(1),))]

            composer.restore([(entry.text, entry.attachments) for entry in leftovers])

            assert composer.text == 'volvé [Image #1]'
            assert len(composer.attachments) == 1
            assert composer.attachments[0].media_type == 'image/png'

    asyncio.run(inner())


def test_restoring_two_messages_renumbers_the_second_ones_images(tmp_path):
    """Each message numbered from one; joined, the numbers have to keep counting."""

    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            composer = _composer(app)

            composer.restore([('uno [Image #1]', (_attachment(1),)), ('dos [Image #1]', (_attachment(1),))])

            assert composer.text == 'uno [Image #1]\ndos [Image #2]'
            assert [image.number for image in composer.attachments] == [1, 2]

    asyncio.run(inner())


# --- sending ----------------------------------------------------------------


def test_enter_sends_the_text_and_the_images_together(tmp_path):
    """The proof is the history: the image is a content part of the user prompt."""

    async def inner() -> None:
        app = _app(tmp_path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press('q', 'u', 'e')
            with Clipboard(_png()):
                await pilot.press('ctrl+v')
                await pilot.pause()
            await pilot.press('enter')

            for _ in range(60):
                await pilot.pause()
                if not app._turn_active:
                    break

            prompt = app._session.messages[0].parts[0]
            assert prompt.content[0] == 'que[Image #1]'
            images = [item for item in prompt.content if not isinstance(item, str)]
            assert len(images) == 1
            assert images[0].media_type == 'image/png'

    asyncio.run(inner())
