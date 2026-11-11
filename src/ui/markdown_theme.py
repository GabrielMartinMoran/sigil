"""Translate the active terminal palette to Rich Markdown and code styles."""

from __future__ import annotations

from collections.abc import Callable

from pygments.token import Token, _TokenType
from rich.style import Style
from rich.syntax import SyntaxTheme
from rich.theme import Theme


class CodeTheme(SyntaxTheme):
    """Highlight code with the same roles used by the surrounding interface."""

    def __init__(self, colors: dict[str, str]) -> None:
        self.colors = colors

    def get_background_style(self) -> Style:
        return Style(color=self.colors['foreground'], bgcolor=self.colors['panel'])

    def get_style_for_token(self, token_type: _TokenType) -> Style:
        colors = self.colors
        if token_type in Token.Comment:
            return Style(color=colors['muted'], italic=True)
        if token_type in Token.Keyword:
            return Style(color=colors['primary'], bold=True)
        if token_type in Token.Literal.String:
            return Style(color=colors['success'])
        if token_type in Token.Literal.Number:
            return Style(color=colors['warning'])
        if token_type in Token.Name.Function or token_type in Token.Name.Class:
            return Style(color=colors['accent'], bold=True)
        if token_type in Token.Operator:
            return Style(color=colors['secondary'])
        if token_type in Token.Error:
            return Style(color=colors['error'])
        return Style(color=colors['foreground'])


def markdown_themes(resolve: Callable[[str], str]) -> tuple[Theme, CodeTheme]:
    """Build scoped Rich styles from the current Textual theme roles."""
    colors = {
        name: resolve(name)
        for name in ('primary', 'secondary', 'accent', 'foreground', 'panel', 'muted', 'success', 'warning', 'error')
    }
    primary, accent, muted = colors['primary'], colors['accent'], colors['muted']
    code = f'{primary} on {colors["panel"]}'
    styles = {
        'markdown.code': code,
        'markdown.code_block': code,
        'markdown.block_quote': muted,
        'markdown.list': primary,
        'markdown.item.number': primary,
        'markdown.hr': muted,
        'markdown.h1.border': primary,
        'markdown.h1': f'bold {primary}',
        'markdown.h2': f'bold {primary}',
        'markdown.h3': f'bold {primary}',
        'markdown.h4': f'bold {accent}',
        'markdown.h5': f'bold {accent}',
        'markdown.h6': f'bold {accent}',
        'markdown.h7': f'italic {muted}',
        'markdown.link': f'underline {accent}',
        'markdown.link_url': f'underline {accent}',
        'markdown.table.border': muted,
        'markdown.table.header': f'bold {primary}',
        'markdown.kbd': f'bold {accent}',
    }
    return Theme(styles), CodeTheme(colors)
