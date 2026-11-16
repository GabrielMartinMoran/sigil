"""The header that groups consecutive tool calls."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar

from rich.style import Style as Click
from rich.text import Text
from textual.widgets import Static

from src.core.tooling.presentation import display_name
from src.ui.render.palette import color
from src.ui.widgets.chat.chat_style import GUTTER, MIN_GROUP_ROWS
from src.ui.widgets.chat.chat_text import format_duration
from src.ui.widgets.chat.rows import fit
from src.ui.widgets.chat.tool_call_block import ToolCallBlock


class ToolGroupHeader(Static):
    """The one line above a batch of calls: `3 tools [2/3]`, then `3 tools · 1 error`.

    A model that asks for three tools in one response produces three rows. On their own
    they read as three unrelated things that happened to arrive together; the header is
    what says they are one step, and how far along it is.

    It is a header and **not a container**. The rows stay direct children of the
    transcript, which is what keeps them independently paintable, clickable (a `summon`
    opens its subagent) and testable. The bar down the left of the group is each row's
    own gutter, already there; a container would have to redraw it and indent
    everything.

    It mutates instead of having a footer. A header *and* a footer would be two lines of
    chrome for one batch, and they can disagree — the mockup that inspired this had
    `1 failed` in one and `all passed` in the other.

    It is hidden while the batch has a single call: one call needs no grouping, and the
    row says everything a header would.
    """

    can_focus = True
    BINDINGS: ClassVar = [('enter', 'toggle_group', 'Tool group'), ('space', 'toggle_group', 'Tool group')]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._rows: list[ToolCallBlock] = []
        self._collapsed = False

    def action_toggle_group(self) -> None:
        if self.closed:
            self._collapsed = not self._collapsed
            self.refresh_from(self._rows)

    def on_click(self, event: Click) -> None:
        event.stop()
        self.action_toggle_group()

    def refresh_from(self, rows: Sequence[ToolCallBlock]) -> None:
        """Repaints from the rows the transcript is holding for this batch."""
        self._rows = list(rows)
        self.display = len(self._rows) >= MIN_GROUP_ROWS
        for index, row in enumerate(self._rows):
            row.set_class(self.display or index > 0, 'tight')
            row.display = not self._collapsed or row.failed or row.pending
        self.update(self._build())

    def on_resize(self) -> None:
        self.update(self._build())

    @property
    def closed(self) -> bool:
        """Whether every row has an outcome: the batch is over."""
        return all(not row.pending for row in self._rows)

    def _build(self) -> Text:
        total = len(self._rows)
        done = sum(not row.pending for row in self._rows)
        errors = sum(row.raised for row in self._rows)
        failed = sum(row.failed and not row.raised for row in self._rows)
        text = Text(GUTTER, style=color(self, 'tool-border'))
        mark = '⋮ ' if not self.closed else '✗ ' if errors or failed else '✓ '
        text.append(mark, style=color(self, 'error' if errors or failed else 'tool-title'))
        text.append(f'{total} tools' + (' completed' if self.closed else ''), style=f'bold {color(self, "tool-title")}')
        badge = self._badge(done, total, errors, failed)
        text.append(f'  {badge}', style=color(self, 'tool-title'))
        elapsed = format_duration(self._longest()) if self.closed else ''
        right = Text(f'{elapsed}  {"›" if self._collapsed else "⌄"}', style=color(self, 'muted'))
        header = fit(text, right, self.size.width)
        if self._collapsed:
            counts: dict[str, int] = {}
            for row in self._rows:
                name = display_name(row.name)
                counts[name] = counts.get(name, 0) + 1
            detail = ' · '.join(f'{count} {name}' for name, count in counts.items())
            header.append(f'\n{GUTTER}  {detail} · collapsed group', style=color(self, 'muted'))
        return header

    def _badge(self, done: int, total: int, errors: int, failed: int) -> str:
        if not self.closed:
            return f'{done} / {total} done'
        if errors or failed:
            return ' · '.join(
                part for part in (f'{errors} error' if errors else '', f'{failed} failed' if failed else '') if part
            )
        if self._rows and all(row._summary is not None and row._summary.status == 'good' for row in self._rows):
            return 'all passed'
        return 'done'

    def _longest(self) -> float | None:
        durations = [row.duration_ms for row in self._rows if row.duration_ms is not None]
        return max(durations) if durations else None
