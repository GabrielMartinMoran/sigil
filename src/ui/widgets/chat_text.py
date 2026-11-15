"""Turning tool arguments and results into the text the chat shows."""

from __future__ import annotations

import re
from typing import Any

from rich.style import Style
from rich.text import Text

from src.core.tooling.outputs import present_output
from src.ui.widgets.chat_style import TOOL_OUTPUT_LINES, TOOL_OUTPUT_WIDTH

_URL = re.compile(r'https?://[^\s<>\[\]()]+')


def preview(value: Any, limit: int = TOOL_OUTPUT_WIDTH) -> str:
    """One line, whitespace collapsed, truncated with an ellipsis."""
    try:
        text = ' '.join(str(value).split())
    except Exception:  # noqa: BLE001 - a broken `__str__` must not break the chat
        text = object.__repr__(value)
    return text if len(text) <= limit else text[: limit - 1] + '…'


def format_duration(duration_ms: float | None) -> str:
    """How long a call took, in one unit and one precision.

    Milliseconds below a second — that is where tools live, and `450ms` reads faster
    than `0.45s` — and seconds with a decimal above it. No third unit: a turn is not
    going to have a tool that takes an hour, and if it does, `3700.0s` says it.
    """
    if duration_ms is None:
        return ''
    if duration_ms < 1000:
        return f'{duration_ms:.0f}ms'
    return f'{duration_ms / 1000:.1f}s'


def format_tool_args(args: Any) -> str:
    """The arguments of a call as a compact, readable line.

    A dict is rendered as ``key=value`` pairs rather than JSON: the braces and the
    quotes cost columns and say nothing a reader needs. A single argument is shown
    on its own, which is what makes `run(command='ls')` read as `run ls`.
    """
    if isinstance(args, dict):
        if not args:
            return ''
        if len(args) == 1:
            (value,) = args.values()
            return preview(value)
        return ', '.join(f'{key}={preview(value, 48)}' for key, value in args.items())
    return preview(args)


def output_lines(value: Any) -> list[str]:
    """The lines of a tool result, ready to be indented under the gutter.

    Two shapes, because the tools have two: a result that carries a process's output
    shows that output, and anything else shows its text. `str` on a `RunToolOutput`
    gives `returncode=0 stdout='...' stderr=None`: a repr that repeats in one long line
    what the row already summarized, and hides the output a failure is about.

    Leading whitespace is kept: a diff, a stack trace or a snippet of code only reads
    correctly with its indentation, and a tool that answers with code is the common
    case. Trailing whitespace is dropped — it is invisible and costs width.
    """
    text = value if isinstance(value, str) else present_output('', value).text
    lines = [line.rstrip() for line in text.splitlines()]
    # A trailing blank line is an artefact of `print`-style output, not content.
    while lines and not lines[-1]:
        lines.pop()
    return lines


def truncate_output(lines: list[str], limit: int = TOOL_OUTPUT_LINES) -> tuple[list[str], int]:
    """Head of ``lines`` plus how many were dropped. Returns ``(shown, omitted)``."""
    shown = [_clip(line) for line in lines[:limit]]
    return shown, max(0, len(lines) - len(shown))


def _clip(line: str, limit: int = TOOL_OUTPUT_WIDTH) -> str:
    """Truncates a line without touching its indentation."""
    return line if len(line) <= limit else line[: limit - 1] + '…'


def link_urls(text: Text) -> Text:
    """Underlines every URL in ``text`` as a link."""
    for match in _URL.finditer(text.plain):
        url = match.group().rstrip('.,;:!?')
        text.stylize(Style(link=url, underline=True), match.start(), match.start() + len(url))
    return text


"""Rows a batch needs before its header is worth drawing."""
