"""The block that shows a model answer."""

from __future__ import annotations

import time
from typing import Any

from rich.console import Group
from rich.text import Text
from textual.events import Click
from textual.selection import Selection
from textual.widgets import Static

from src.ui.palette import color
from src.ui.widgets.agent_tree_rows import INDENT
from src.ui.widgets.chat_style import REASONING_MARKER, RENDER_INTERVAL
from src.ui.widgets.linked_markdown import LinkedMarkdown


class AssistantBlock(Static):
    """One model response block: collapsed reasoning plus the Markdown answer.

    The Markdown is re-rendered at most every `RENDER_INTERVAL` seconds so as not
    to re-parse it on every delta; a final flush is guaranteed with `finalize()`.
    """

    def __init__(self, *, reasoning_visible: bool = False, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._thinking = ''
        self._has_thinking = False
        self._text = ''
        self._reasoning_visible = reasoning_visible
        self._dirty = False
        self._last_render = 0.0
        self._timer: Any = None

    @property
    def reasoning_visible(self) -> bool:
        return self._reasoning_visible

    def set_reasoning_visible(self, visible: bool) -> None:
        """Expands or collapses the reasoning without touching the answer."""
        if visible == self._reasoning_visible:
            return
        self._reasoning_visible = visible
        self._flush()

    def add_thinking(self, delta: str) -> None:
        self._has_thinking = True
        self._thinking += delta
        self._schedule_refresh()

    def add_text(self, delta: str) -> None:
        self._text += delta
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        elapsed = time.monotonic() - self._last_render
        if elapsed >= RENDER_INTERVAL or not self.is_mounted:
            # Nothing to schedule against before mount: `finalize()` paints.
            self._flush()
            return

        self._dirty = True
        if self._timer is None:
            self._timer = self.set_timer(RENDER_INTERVAL - elapsed, self._on_timer)

    def _on_timer(self) -> None:
        self._timer = None
        if self._dirty:
            self._flush()

    def _flush(self) -> None:
        self._dirty = False
        self._last_render = time.monotonic()
        self._paint()

    def _paint(self) -> None:
        """Pushes the built renderable to the widget, if there is a widget to push to.

        A block is a plain object until it is mounted, and `update()` needs a running
        app. Guarding here keeps the block constructible on its own — which is how
        the tests exercise the rendering — and the first flush after mount catches up.
        """
        if self.is_mounted:
            self.update(self._build())

    def on_mount(self) -> None:
        self._flush()

    def finalize(self) -> None:
        """Flushes the pending content right away (end of the turn)."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._flush()

    def repaint(self) -> None:
        """Rebuilds the content after a theme change; see `ChatLog.repaint`."""
        self._flush()

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        console = self.app.console
        options = console.options.update_width(max(1, self.content_size.width))
        rendered = ''.join(segment.text for segment in console.render(self._build(), options) if not segment.control)
        return selection.extract(rendered), '\n'

    def on_click(self, event: Click) -> None:
        url = event.style.link if event.style else None
        if url and url.startswith(('https://', 'http://')):
            self.app.open_url(url)
            event.stop()

    # --- rendering ----------------------------------------------------------

    def _build(self) -> Group:
        parts: list[object] = []
        if self._has_thinking:
            parts.append(self._reasoning_renderable())
        if self._text:
            parts.append(LinkedMarkdown(self._text, self))
        if not parts:
            return Text('')
        if len(parts) == 1:
            return parts[0]
        return Group(*parts)

    def _reasoning_renderable(self) -> Text:
        """The reasoning, expanded or as a one-line marker.

        The marker keeps the presence and size of hidden reasoning visible without
        repeating command help throughout the transcript.
        """
        if not self._reasoning_visible:
            return self._collapsed_marker()

        text = Text()
        text.append(f'{REASONING_MARKER} ', style=color(self, 'reasoning'))
        text.append('Reasoning\n', style=f'italic {color(self, "reasoning")}')
        for line in (self._thinking or 'unavailable').splitlines():
            text.append(INDENT, style=color(self, 'reasoning'))
            text.append(f'{line}\n', style=f'italic {color(self, "reasoning")}')
        return text

    def _collapsed_marker(self) -> Text:
        text = Text()
        text.append(f'{REASONING_MARKER} ', style=color(self, 'reasoning'))
        text.append('Reasoning', style=f'italic {color(self, "reasoning")}')
        if self._thinking:
            text.append(f' · {self._reasoning_size()}', style=color(self, 'muted'))
        return text

    def _reasoning_size(self) -> str:
        """``12 lines``, or ``1 line``. Words, not a token count nobody can verify."""
        count = len(self._thinking.splitlines()) or 1
        return f'{count} line' if count == 1 else f'{count} lines'
