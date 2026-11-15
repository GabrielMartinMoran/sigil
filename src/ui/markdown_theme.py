"""Translate the active terminal palette to Rich Markdown and code styles."""

from __future__ import annotations

from collections.abc import Callable

from rich.theme import Theme

from src.ui.code_theme import CodeTheme


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
