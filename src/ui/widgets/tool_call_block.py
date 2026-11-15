"""The block that shows one tool call and its result."""

from __future__ import annotations

import json
import time
from typing import Any, ClassVar

from rich.console import Group
from rich.panel import Panel
from rich.style import Style as Click
from rich.text import Text
from textual.timer import Timer
from textual.widgets import Static

from src.core.files.text import preview
from src.core.tooling.outputs import present_output
from src.core.tooling.presentation import ToolSummary, display_name
from src.ui.palette import color
from src.ui.widgets.chat_style import FAILURE_LINES, GUTTER
from src.ui.widgets.chat_text import format_duration, format_tool_args, link_urls, output_lines, truncate_output
from src.ui.widgets.rows import fit
from src.ui.widgets.status_bar import SPINNER_FRAMES, SPINNER_INTERVAL


class ToolCallBlock(Static):
    """A tool call and its result, drawn as one indented block.

    The header carries the call and, flush right, what came of it: how long it took
    and the one line the tool chose to describe its result (`9 passed in 0.45s`,
    `3 replacements`). That summary comes from the tool itself — see
    `core.tooling.presentation` — and not from `str(result)`, which is how a
    Pydantic repr used to end up in the middle of a conversation.

    The glyph and color summarize the user-visible outcome: `✓` for a completed
    call, `✗` for a raised call or a declared bad result. The detail distinguishes
    a failed test from a tool exception; both deserve a visible failure mark.

    The body carries the beginning of the output, dimmed.
    """

    can_focus = True
    BINDINGS: ClassVar = [('enter', 'toggle_output', 'Tool output'), ('space', 'toggle_output', 'Tool output')]

    #: Fields the subclass needs before the first paint.
    _pending_state: bool = True

    def __init__(self, name: str, args: Any, expanded: bool = False, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._name = name
        self._args = args
        self._expanded = expanded
        self._result: Any = None
        self._ok = True
        self._error: str | None = None
        self._pending = True
        self._pending_frame = 0
        self._pending_started = time.monotonic()
        self._pending_timer: Timer | None = None
        self._summary: ToolSummary | None = None
        self._duration_ms: float | None = None
        self._prepare()
        self._update_content()

    def _prepare(self) -> None:
        """Hook for a subclass that needs its own state before the first paint.

        `__init__` paints once at the end, and a subclass that set its fields after
        `super().__init__` would be painted with none of them.
        """

    def on_mount(self) -> None:
        if self._pending:
            self._pending_timer = self.set_interval(SPINNER_INTERVAL, self._advance_pending)

    def on_unmount(self) -> None:
        self._stop_pending_timer()

    def _advance_pending(self) -> None:
        if self._pending:
            self._pending_frame = (self._pending_frame + 1) % len(SPINNER_FRAMES)
            self._update_content()

    def _stop_pending_timer(self) -> None:
        if self._pending_timer is not None:
            self._pending_timer.stop()
            self._pending_timer = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def pending(self) -> bool:
        """Whether the call has not produced an outcome yet."""
        return self._pending

    @property
    def raised(self) -> bool:
        """Whether the call itself failed, as opposed to returning a bad result."""
        return self._error is not None

    @property
    def duration_ms(self) -> float | None:
        return self._duration_ms

    def set_result(self, output: Any, *, ok: bool = True) -> None:
        shown = present_output(self._name, output)
        self._result = shown.text
        if self._summary is None:
            self._summary = shown.summary
        self._ok = ok
        self._pending = False
        self._stop_pending_timer()
        self._update_content()

    def set_error(self, message: str) -> None:
        self._error = message
        self._ok = False
        self._pending = False
        self._stop_pending_timer()
        self._update_content()

    def set_summary(self, summary: ToolSummary | None, duration_ms: float | None = None) -> None:
        """Records what the tool said about itself, and how long it took.

        It arrives from the tool event, which is emitted when the tool returns — before
        the run streams the result back. The block can therefore say "3 replacements,
        120 ms" while the output is still on its way to the body.
        """
        self._summary = summary
        self._duration_ms = duration_ms
        self._update_content()

    def set_expanded(self, expanded: bool) -> None:
        """Shows or hides the output of the call."""
        self._expanded = expanded
        self._update_content()

    def action_toggle_output(self) -> None:
        self.set_expanded(not self._expanded)

    def on_click(self, event: Click) -> None:
        event.stop()
        url = event.style.link if event.style else None
        if url and url.startswith(('https://', 'http://')):
            self.app.open_url(url)
            return
        self.action_toggle_output()

    @property
    def failed(self) -> bool:
        return not self._ok or self._failed

    def repaint(self) -> None:
        """Rebuilds the block after a theme change; see `ChatLog.repaint`."""
        self._update_content()

    def on_resize(self) -> None:
        """Rebuilds the header: the right column is aligned to the current width."""
        self._update_content()

    def _update_content(self) -> None:
        self.update(self._build())

    def _build(self) -> Text:
        body = [link_urls(line) for line in self._body()]
        if self._expanded and body:
            output = Text('\n').join(line[len(GUTTER) :] for line in body)
            if self._name == 'run':
                command = Text(f'$ {self._argument_hint()}\n', style=color(self, 'tool-title'))
                output = command + output
            panel = Panel(output, border_style=color(self, 'tool-border'), padding=(0, 1))
            return Group(self._header(), panel)
        return Group(self._header(), *body)

    def _header(self) -> Text:
        """The call on the left, the outcome flush right, on the block's own width.

        The state mark goes **first**, right after the gutter, and not at the end of the
        call. At the end it was the first thing a narrow row clipped — the row that most
        needed to say "this one failed" was the one that lost its mark. Leading it also
        matches how the call reads: the outcome of the call is what is being scanned.
        """
        border = color(self, 'tool-border')
        text = Text()
        text.append(GUTTER, style=border)
        state_style = color(self, 'error' if self.failed else 'success') if not self._pending else border
        text.append(f'{self._state_mark()} ', style=state_style)
        name = display_name(self._name)
        text.append(name.ljust(12), style=color(self, 'tool-title'))
        arguments = self._argument_hint()
        if arguments:
            text.append(f' {arguments}', style=color(self, 'text'))
        return fit(text, self._outcome(), self.size.width)

    def _argument_hint(self) -> str:
        args = self._args
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                return preview(args)
        if isinstance(args, dict):
            for key in ('cmds', 'command', 'path', 'query', 'url'):
                value = args.get(key)
                if isinstance(value, list):
                    return preview(' ; '.join(str(item) for item in value))
                if isinstance(value, str) and value:
                    return preview(value)
        return format_tool_args(args)

    def _outcome(self) -> Text | None:
        """`9 passed in 0.45s · 1.2s`: the tool's summary, then how long it took.

        While running, show a live elapsed time instead of an outcome. After a
        reload, no duration is invented: it lives in the event, not in storage.
        """
        if self._pending:
            elapsed = max(0, int(time.monotonic() - self._pending_started))
            return Text(f'running · {elapsed}s', style=color(self, 'muted'))
        parts: list[tuple[str, str]] = []
        if self._summary is not None:
            parts.append((self._summary.text, self._status_style(self._summary.status)))
        elapsed = format_duration(self._duration_ms)
        if elapsed:
            parts.append((elapsed, color(self, 'muted')))
        if self._result is not None or self._error:
            parts.append(('⌄' if self._expanded else '›', color(self, 'muted')))
        if not parts:
            return None
        text = Text(no_wrap=True, overflow='ellipsis')
        for index, (content, style) in enumerate(parts):
            if index:
                text.append(' · ', style=color(self, 'muted'))
            text.append(content, style=style)
        return text

    def _status_style(self, status: str) -> str:
        """A bad result is red, a good one green, an unknown one muted.

        The summary preserves the declared status; the glyph also marks a bad
        result as failed, without turning it into a tool exception.
        """
        return {'good': color(self, 'success'), 'bad': color(self, 'error')}.get(status, color(self, 'muted'))

    def _state_mark(self) -> str:
        """An animated mark while running, `✓` or `✗` after; always one cell."""
        if self._pending:
            return SPINNER_FRAMES[self._pending_frame]
        return '✗' if self.failed else '✓'

    @property
    def _failed(self) -> bool:
        """Whether the tool reported a bad result, as opposed to raising."""
        return self._summary is not None and self._summary.status == 'bad'

    def _body(self) -> list[Text]:
        """The output: expanded, or collapsed to one line.

        Collapsed by default, and **a failure is never fully collapsed**. A tool that
        raised shows its error; a call whose summary came back `bad` shows the first
        lines that explain it. Hiding a failure behind a preference nobody has set yet
        is hiding the one thing the reader has to act on, so the exception lives here
        and not in the preference.
        """
        if self._error:
            return [self._line(f'↳ {self._error}', color(self, 'diff-removed'))]
        if self._result is None:
            return []

        shown, omitted = truncate_output(output_lines(self._result))
        if not shown:
            return []
        total = len(shown) + omitted
        if self._expanded:
            lines = [self._line(line, color(self, 'tool-output')) for line in shown]
            if omitted:
                lines.append(self._line(f'… +{omitted} more', color(self, 'muted')))
            return lines
        if self._failed:
            lines = [self._line(f'↳ {line}', color(self, 'diff-removed')) for line in shown[:FAILURE_LINES]]
            if total > FAILURE_LINES:
                lines.append(self._line(f'… +{total - FAILURE_LINES} more', color(self, 'muted')))
            return lines
        if self._summary is not None:
            # The row already says what came back. A `… 12 lines` under a row that
            # reads `9 passed in 0.45s` is a second line for nothing.
            return []
        # No presenter and no preference to expand: say that there *is* output, so the
        # row is not a ✓ with nothing behind it.
        return [self._line(f'… {total} line' + ('s' if total != 1 else ''), color(self, 'muted'))]

    def _line(self, content: str, style: str) -> Text:
        text = Text()
        text.append(GUTTER, style=color(self, 'tool-border'))
        text.append(content, style=style)
        return text
