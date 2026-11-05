"""Shared helpers for the UI tests.

`render_text` exists because the widgets paint with Rich renderables, and comparing
against `str(renderable)` only ever prints a repr. Tests want the text a reader would
see, which is what a console with no color captures.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console


def render_text(renderable: Any, *, width: int = 200) -> str:
    """Plain text of a Rich renderable, colors dropped.

    The console has to be wide: a narrow one wraps the lines and the assertion then
    fails on a line break instead of on the content.
    """
    console = Console(width=width, force_terminal=False, color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()
