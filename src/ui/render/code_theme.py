"""CodeTheme message of the terminal."""

from __future__ import annotations

from pygments.token import Token, _TokenType
from rich.style import Style
from rich.syntax import SyntaxTheme


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
