"""Markdown and syntax highlighting use the active palette, not Rich defaults."""

from io import StringIO
from typing import ClassVar

from rich.color import Color
from rich.console import Console

from src.ui.widgets.chat_log import LinkedMarkdown


class App:
    theme_variables: ClassVar[dict[str, str]] = {
        'primary': '#112233',
        'secondary': '#223344',
        'accent': '#334455',
        'foreground': '#445566',
        'panel': '#ddeeff',
        'muted': '#556677',
        'success': '#667788',
        'warning': '#778899',
        'error': '#8899aa',
    }


class Widget:
    app = App()


def test_markdown_palette_covers_tables_quotes_links_and_code_without_leaking():
    console = Console(file=StringIO(), force_terminal=True, width=80)
    default_style = console.get_style('markdown.h2')
    markdown = LinkedMarkdown(
        '## Title\n\n> Quote\n\n| Header |\n|---|\n| Cell |\n\n'
        '[Link](https://example.com) https://example.org\n\n'
        '```python\ndef example():\n    return "text"\n```',
        Widget(),
    )
    segments = list(console.render(markdown))

    def segment(text):
        return next(s for s in segments if text in s.text and s.style and s.style.color)

    assert segment('Title').style.color == Color.parse('#112233')
    assert segment('Quote').style.color == Color.parse('#556677')
    assert segment('Header').style.color == Color.parse('#112233')
    assert segment('def').style.color == Color.parse('#112233')
    assert segment('def').style.bgcolor == Color.parse('#ddeeff')
    assert segment('text').style.color == Color.parse('#667788')
    assert any(s.style and s.style.link == 'https://example.com' for s in segments)
    assert any(s.style and s.style.link == 'https://example.org' for s in segments)
    assert console.get_style('markdown.h2') == default_style
