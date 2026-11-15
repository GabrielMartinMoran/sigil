"""Markdown with clickable links."""

from __future__ import annotations

import re
from typing import Any

from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown
from rich.segment import Segment
from rich.style import Style

from src.ui.markdown_theme import markdown_themes
from src.ui.palette import color

_URL = re.compile(r'https?://[^\s<>\[\]()]+')


class LinkedMarkdown:
    """Render Markdown while adding terminal links to bare HTTP URLs."""

    def __init__(self, text: str, widget: Any = None) -> None:
        self.theme, code_theme = markdown_themes(lambda name: color(widget, name))
        self.markdown = Markdown(text, code_theme=code_theme)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        with console.use_theme(self.theme):
            yield from self._render_linked(console, options)

    def _render_linked(self, console: Console, options: ConsoleOptions) -> RenderResult:
        for segment in console.render(self.markdown, options):
            if segment.control or (segment.style and segment.style.link):
                yield segment
                continue
            start = 0
            for match in _URL.finditer(segment.text):
                url = match.group().rstrip('.,;:!?')
                yield Segment(segment.text[start : match.start()], segment.style)
                yield Segment(url, (segment.style or Style()) + Style(link=url, underline=True))
                start = match.start() + len(url)
            yield Segment(segment.text[start:], segment.style)
