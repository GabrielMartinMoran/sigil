"""Bottom bar input: a multi-line composer with the command menu and shortcuts.

Four things are handled here rather than as app-level bindings, and the reason is
the same for all of them: **the focused widget sees the key first**.

- The command menu (Up/Down/Tab/Esc) needs `priority`, because the text area
  consumes those keys for editing and cursor movement.
- `ctrl+m`, `ctrl+l`, `ctrl+a` and `ctrl+v` are handled in `_on_key`.
- `Enter` sends and `Shift+Enter` inserts a newline. The text area already treats
  Enter as a newline, so the split is decided in `_on_key`, before the area turns
  the key into an edit.
- `Esc` asks to stop the running turn, and any other key calls that request off.

A `priority` binding is checked by the app before the key reaches the widget, so
the actions below are where the menu keys land, not `_on_key`. `_on_key` sees
everything else: the printable keys, the editing keys, and the shortcuts.

Why `ctrl+m` needs care: in a legacy terminal, Ctrl+M and Enter are the same byte
(CR, `0x0D`), so a global `ctrl+m` binding silently swallows Enter — which is how a
message is sent. Textual enables the Kitty keyboard protocol with the
"disambiguate escape codes" flag, which makes a capable terminal send
`CSI 109;5u` for Ctrl+M (distinct from `\\r` for Enter), but terminals without it
still collapse the two. Handling the key here means:

- a capable terminal sends `ctrl+m` → the model picker opens;
- a legacy terminal sends `enter` → the message is sent, exactly as before.

`Shift+Enter` has a related, milder constraint: it only exists as a distinct key on
a terminal that reports modifiers. Without them the terminal sends a bare CR and
the key reads as `enter`, which sends the message. That degrades into the familiar
behaviour rather than into a lost keystroke, so it needs no fallback.

Why `Esc` lives here and not in the app: the abort has to coexist with the `Esc`
that cancels a question panel, which is the panel's own binding. A global
`priority` binding at the app level would run before both of them and swallow
them.

Pasting an image is the other thing that lives here, because clipboard access is
not a keystroke the app can read: `ctrl+v` means "look at the clipboard", and a
terminal only carries text. See `src/engine/attachments`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.binding import Binding
from textual.document._edit import Edit
from textual.message import Message
from textual.strip import Strip
from textual.widgets import TextArea

from src.engine.attachments.clipboard import ClipboardUnavailable, read_clipboard_image
from src.engine.attachments.image_attachment import ImageAttachment
from src.engine.attachments.images import ImageError, prepare_image
from src.engine.attachments.tokens import (
    TOKEN_PATTERN,
    block_deletion,
    location,
    numbers_in,
    offset,
    remap,
    renumber,
)
from src.ui.widgets.reading import (
    SCROLL_DOWN_KEY,
    SCROLL_LINES,
    SCROLL_UP_KEY,
    current_pane,
    page_lines,
    scroll_pane,
)

CTRL_MODELS = 'ctrl+m'
"""Ctrl+M. Same byte as Enter on a legacy terminal; see the module docstring."""

CTRL_EFFORT = 'ctrl+l'
"""Ctrl+T. Free of ambiguity, but Ctrl+T is `transpose-chars` in readline."""

CTRL_AGENT = 'ctrl+a'
"""Ctrl+A. `home` in the `TextArea` (and in readline), which is why it is read
here: the prompt claims it before the widget turns it into a cursor move."""

CTRL_PASTE = 'ctrl+v'
"""Ctrl+V. The one key that can carry an image; see `_paste`."""

NEWLINE_KEYS = frozenset({'shift+enter'})
"""Keys that insert a line break instead of sending. See the module docstring."""

SCROLL_KEYS = {SCROLL_UP_KEY: -1, SCROLL_DOWN_KEY: 1}
"""The pair that reads the conversation: the same one the question panel binds."""

DELETE_KEYS = frozenset({'backspace', 'delete', 'ctrl+h', 'ctrl+w'})
"""Keys whose deletion may have to take a whole `[Image #n]` with it."""

TOKEN_STYLE = Style(bold=True, reverse=True)
"""How the marker is drawn in the composer.

Reverse video and not a foreground color: the composer is a `TextArea` painted by
the stylesheet, which a Rich color cannot reach, and reverse video is legible on
every theme — including the ones a user installs later.
"""


class PromptInput(TextArea):
    """The message composer: Enter sends, Shift+Enter opens a new line.

    It is a `TextArea` and not an `Input` because a message with line breaks is a
    document, not a field: a single-line field cannot show the second line, and
    the alternative — a hidden buffer with a fake display — would have to
    reimplement wrapping, selection and cursor movement.

    It also owns the images pasted into the message. The bytes never enter the
    document: the text carries `[Image #n]` and `attachments` keeps the rest, so
    what the user edits stays a document and what travels to the model stays a
    list of content parts.
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('up', 'menu_up', show=False, priority=True),
        Binding('down', 'menu_down', show=False, priority=True),
        Binding('tab', 'menu_complete', show=False, priority=True),
        Binding('escape', 'menu_escape', show=False, priority=True),
        # `TextArea` already binds both of these to its own cursor movement, and a
        # priority binding is the only way to see them first. The transcript is what
        # a reader of this app pages through; the cursor is one message away.
        Binding('pageup', 'transcript_page(-1)', show=False, priority=True),
        Binding('pagedown', 'transcript_page(1)', show=False, priority=True),
    ]

    class Submitted(Message):
        """The user pressed Enter with a message worth sending.

        It carries the text and the images and not the widget: by the time the
        app handles it the composer is already empty, and the turn needs what was
        in it.
        """

        def __init__(self, value: str, attachments: tuple[ImageAttachment, ...] = ()) -> None:
            super().__init__()
            self.value = value
            self.attachments = attachments

    class AttachmentRejected(Message):
        """An image could not be attached, with the reason to show the user."""

        def __init__(self, reason: str) -> None:
            super().__init__()
            self.reason = reason

    def __init__(
        self,
        *,
        placeholder: str = '',
        id: str | None = None,
        classes: str | None = None,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            placeholder=placeholder,
            id=id,
            classes=classes,
            disabled=disabled,
            soft_wrap=True,
            show_line_numbers=False,
            # Tab belongs to the command menu, never to the document.
            tab_behavior='focus',
            highlight_cursor_line=False,
        )
        self._attachments: dict[int, ImageAttachment] = {}
        self._next_number = 1
        self._last_submission: tuple[str, tuple[ImageAttachment, ...]] | None = None

    # --- attachments ---------------------------------------------------------

    @property
    def attachments(self) -> tuple[ImageAttachment, ...]:
        """The images the message carries, in the order the text mentions them."""
        numbers = numbers_in(self.text)
        return tuple(self._attachments[number] for number in numbers if number in self._attachments)

    def attach(self, attachment: ImageAttachment) -> ImageAttachment:
        """Adds an image to the message and writes its marker at the cursor.

        The marker is inserted like typed text, so it lands inside the undo
        history and the deletion that follows is one edit like any other.
        """
        numbered = self._numbered(attachment, self._next_number)
        self._next_number += 1
        self._attachments[numbered.number] = numbered
        self.insert(numbered.token)
        return numbered

    @staticmethod
    def _numbered(attachment: ImageAttachment, number: int) -> ImageAttachment:
        """The same image under another number.

        The number lives on the attachment and the attachment is frozen, so a copy
        is the honest way to renumber one.
        """
        return ImageAttachment(
            number=number,
            data=attachment.data,
            media_type=attachment.media_type,
            width=attachment.width,
            height=attachment.height,
            image_id=attachment.image_id,
        )

    def attach_path(self, path: str | Path) -> ImageAttachment:
        """Attaches an image from disk, with the same bounds as a pasted one."""
        return self.attach(prepare_image(Path(path).read_bytes()))

    def restore(self, entries: Sequence[tuple[str, Sequence[ImageAttachment]]]) -> None:
        """Puts messages back in the composer, images included.

        It exists for the message that missed the last seam of a turn: it goes back
        to the prompt so the next `Enter` sends it, and a message whose text came
        back without its picture would point at an `[Image #1]` that no longer
        exists. Each entry was numbered starting at one, so the images are
        renumbered as the entries are appended and the markers follow them.
        """
        lines: list[str] = []
        offset = 0
        for text, attachments in entries:
            mapping = {attachment.number: offset + index for index, attachment in enumerate(attachments, start=1)}
            offset += len(attachments)
            for attachment in attachments:
                number = mapping[attachment.number]
                self._attachments[number] = self._numbered(attachment, number)
            lines.append(remap(text, mapping))
        self._next_number = offset + 1
        self.value = '\n'.join(lines)

    def discard(self, number: int) -> None:
        """Forgets an image whose marker is no longer in the text."""
        self._attachments.pop(number, None)

    def _sync_attachments(self) -> None:
        """Drops the images the text no longer mentions and renumbers what is left.

        Called after every edit. The numbers are the contract with the model, so
        they cannot be left pointing at an image that was deleted, nor skip a
        number because one in the middle went away.

        The rewrite of the text is bookkeeping the user did not ask for, so it is
        applied without entering the undo history, and the cursor keeps its
        document offset instead of jumping to the end of the message.
        """
        mentioned = numbers_in(self.text)
        alive = [number for number in mentioned if number in self._attachments]
        for number in [number for number in self._attachments if number not in alive]:
            del self._attachments[number]

        if alive != list(range(1, len(alive) + 1)):
            rewritten = renumber(self.text, alive)
            if rewritten != self.text:
                cursor = offset(self.text, self.cursor_location)
                history = self.history
                self.load_text(rewritten)
                self.history = history
                self.move_cursor(location(rewritten, min(cursor, len(rewritten))))

        # `alive` still names the old numbers, in the order the text mentions
        # them; the new dict is keyed by the position each one now occupies.
        self._attachments = {
            new: ImageAttachment(
                number=new,
                data=self._attachments[old].data,
                media_type=self._attachments[old].media_type,
                width=self._attachments[old].width,
                height=self._attachments[old].height,
                image_id=self._attachments[old].image_id,
            )
            for new, old in enumerate(alive, start=1)
        }
        self._next_number = len(self._attachments) + 1

    # --- keys ---------------------------------------------------------------

    async def _on_key(self, event: events.Key) -> None:
        """Enter sends, Shift+Enter breaks the line, and the shortcuts open screens.

        It runs before `TextArea`'s own handler, which would insert `\\n` for Enter
        and consume the key. The picker openers run in a worker because they use
        `push_screen_wait`, which requires one.
        """
        # Typing means "I am doing something else": a confirmation that survives
        # other keys would confirm itself by accident.
        self.app.cancel_abort()
        if event.key in NEWLINE_KEYS:
            event.stop()
            event.prevent_default()
            self.insert('\n')
            return

        if event.key == 'enter':
            event.stop()
            event.prevent_default()
            if self.app.menu_visible:
                # Enter on an open menu picks the highlighted row; that path goes
                # through `menu_choose`, which is guarded like the typed one.
                await self.app.menu_choose()
            else:
                await self.action_submit()
            return

        if event.key == 'ctrl+p':
            event.stop()
            event.prevent_default()
            self.run_worker(self.app.open_command_palette())
            return

        if event.key == CTRL_MODELS:
            event.stop()
            event.prevent_default()
            # Through the dispatcher, not straight into the picker: the key is
            # another door into the same room, and an attached terminal has to
            # answer it exactly as the typed command would.
            self.run_worker(self.app.handle_command('/model'))
            return

        if event.key == CTRL_EFFORT:
            event.stop()
            event.prevent_default()
            self.run_worker(self.app.handle_command('/effort'))
            return
        if event.key == CTRL_AGENT:
            event.stop()
            event.prevent_default()
            self.run_worker(self.app.handle_command('/agent'))
            return
        if event.key == CTRL_PASTE:
            event.stop()
            event.prevent_default()
            self._paste()
            return

        if event.key in DELETE_KEYS and self._delete_block(event.key):
            event.stop()
            event.prevent_default()
            return

        # The conversation is read with the same two keys the question panel binds,
        # so muscle memory survives whichever of the two owns the keyboard.
        step = SCROLL_KEYS.get(event.key)
        if step is not None:
            event.stop()
            event.prevent_default()
            self.action_transcript_scroll(step)
            return

        await super()._on_key(event)

    def _delete_block(self, key: str) -> bool:
        """Deletes a whole `[Image #n]` when the key is aimed at one.

        Returns:
            ``True`` when the deletion was performed here, so the key must not
            reach the `TextArea` and delete one character of the marker.
        """
        deletion = block_deletion(self.text, self.selection, backspace=key != 'delete')
        if deletion is None:
            return False
        old = self.text
        start = location(old, deletion.start)
        end = location(old, deletion.end)
        self.replace('', start, end)
        self.move_cursor(start)
        return True

    def _paste(self) -> None:
        """`ctrl+v`: an image from the clipboard if there is one, the text otherwise.

        The image wins when both are offered, because a screenshot on the
        clipboard is never accompanied by useful text, and a terminal that
        carries only text still reaches `action_paste` through the fallback.
        """
        try:
            raw = read_clipboard_image()
        except ClipboardUnavailable as exc:
            # The tool is missing, not the image: say so once and let the text
            # paste happen, or the user loses the ability to paste at all.
            self.post_message(self.AttachmentRejected(str(exc)))
            raw = None
        if raw is None:
            self.action_paste()
            return
        if not self.app.accepts_images():
            self.post_message(self.AttachmentRejected(self.app.images_refusal()))
            return
        try:
            prepared = prepare_image(raw)
        except ImageError as exc:
            self.post_message(self.AttachmentRejected(str(exc)))
            return
        self.attach(prepared)

    # --- rendering -----------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        """Draws the markers highlighted, on top of what the text area painted.

        Styling the line in `get_line` would be simpler, but it re-parses on every
        line of every repaint; the rendered strip is already split into segments,
        so the markers are found in a handful of strings instead of the whole
        document.
        """
        strip = super().render_line(y)
        segments = strip._segments
        if not any(TOKEN_PATTERN.search(segment.text) for segment in segments):
            return strip
        highlighted: list[Segment] = []
        for segment in segments:
            highlighted.extend(_split_marker(segment.text, segment.style))
        return Strip(highlighted, strip.cell_length)

    # --- value --------------------------------------------------------------

    @property
    def value(self) -> str:
        """The text in the composer.

        `TextArea` calls its content `text`; the app and the tests speak of the
        value of a field, and keeping that name means the composer can replace an
        `Input` without the callers noticing.
        """
        return self.text

    @value.setter
    def value(self, text: str) -> None:
        self.load_text(text)
        self.move_cursor(self.document.end)

    def clear(self) -> None:
        """Empties the composer, its images included, and resets the cursor."""
        self.load_text('')
        self._attachments.clear()
        self._next_number = 1
        self.move_cursor((0, 0))

    # --- actions ------------------------------------------------------------

    async def action_submit(self) -> None:
        """Sends what is in the composer, unless it is only whitespace."""
        text = self.text.strip()
        attachments = self.attachments if text else ()
        if not text:
            return
        # A message that is only a pasted image has no text of its own: the
        # marker it was written with is the whole message.
        self.remember_submission(text, attachments)
        self.clear()
        self.post_message(self.Submitted(text, attachments))

    def remember_submission(self, text: str, attachments: tuple[ImageAttachment, ...] = ()) -> None:
        """Keeps the last sent message or executed menu command for recall."""
        self._last_submission = (text, attachments)

    async def action_menu_up(self) -> None:
        self.app.cancel_abort()
        if self.app.menu_visible:
            self.app.menu_move(-1)
        elif not self.text and self._last_submission is not None:
            self.restore([self._last_submission])
        else:
            self.action_cursor_up()

    async def action_menu_down(self) -> None:
        self.app.cancel_abort()
        if self.app.menu_visible:
            self.app.menu_move(1)
        else:
            self.action_cursor_down()

    async def action_menu_complete(self) -> None:
        """Tab completes the highlighted command instead of running it.

        `Enter` is the one that executes; Tab only fills the word in, and appends
        a trailing space when the command takes an argument, so the next keystroke
        is the argument rather than the rest of the name.
        """
        self.app.cancel_abort()
        if self.app.menu_visible:
            self.app.menu_complete()
            return
        # No menu: nothing to complete, and the focus must stay in the prompt.
        self.app.action_tab_noop()

    async def action_menu_escape(self) -> None:
        """Esc: closes the menu, then goes back or asks to stop the turn.

        Inside a node the press is the way back to whoever summoned it, which is what
        `Esc` means in a view that replaced the conversation; a run in flight does not
        change that, and the alternative is a reader who can never leave. Everywhere
        else it keeps its behaviour: closing the menu, and asking to stop the turn —
        a no-op with nothing running.
        """
        self.app.menu_hide()
        if self.app.subagent_open:
            self.app.cancel_abort()
            self.app.run_worker(self.app.open_parent())
            return
        if self.app.request_abort():
            self.app.abort_turn()

    def action_transcript_page(self, direction: int) -> None:
        """`pgup` / `pgdn`: a page of the conversation, keyboard still here.

        A page is the visible height of the pane, so the reader lands where the
        screen suggests instead of on a number of lines chosen by a constant.
        """
        pane = current_pane(self.screen)
        if pane is None:
            return
        self.app.cancel_abort()
        scroll_pane(pane, direction * page_lines(pane))

    def action_transcript_scroll(self, direction: int) -> None:
        """`ctrl+↑` / `ctrl+↓`: a few lines of the conversation."""
        pane = current_pane(self.screen)
        if pane is None:
            return
        scroll_pane(pane, direction * SCROLL_LINES)

    # --- edits --------------------------------------------------------------

    def edit(self, edit: Edit):  # type: ignore[no-untyped-def]
        """Runs the edit and keeps the attachments in sync with the text."""
        result = super().edit(edit)
        self._sync_attachments()
        return result


def _split_marker(text: str, style: Style | None) -> list[Segment]:
    """Splits one painted segment so the markers in it get `TOKEN_STYLE`."""
    if not TOKEN_PATTERN.search(text):
        return [Segment(text, style)]
    pieces: list[Segment] = []
    position = 0
    for match in TOKEN_PATTERN.finditer(text):
        if match.start() > position:
            pieces.append(Segment(text[position : match.start()], style))
        base = style or Style()
        pieces.append(Segment(match.group(0), base + TOKEN_STYLE))
        position = match.end()
    if position < len(text):
        pieces.append(Segment(text[position:], style))
    return pieces
