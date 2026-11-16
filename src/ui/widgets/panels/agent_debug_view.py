"""Content-free context and cache diagnostics shown in place of the chat."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from src.core.context.meter.debug_report import DebugReport
from src.ui.widgets.panels.debug_body import build_debug_body, build_debug_header
from src.ui.widgets.panels.read_only_scroll import ReadOnlyScroll


class AgentDebugView(Vertical):
    """Chat replacement: fixed header and scrollable body with the diagnostic.

    The header stays fixed because the summary (log vs view, cache hit) is what
    you look at while the turn runs; the body starts at the top and is scrolled
    by hand, without anchoring to the bottom: here the bottom is not the newest part.
    """

    DEFAULT_CSS = """
    AgentDebugView {
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._report: DebugReport | None = None
        self._header = Static(Text(''), id='debug-header')
        self._body = Static(Text(''), id='debug-body')

    def compose(self) -> ComposeResult:
        yield self._header
        with ReadOnlyScroll(id='debug-scroll'):
            yield self._body

    def on_mount(self) -> None:
        self.query_one('#debug-scroll', ReadOnlyScroll).scroll_home(animate=False)

    @property
    def last_report(self) -> DebugReport | None:
        """Last painted report. Tests use it to inspect without parsing the screen."""
        return self._report

    def show_unavailable(self, message: str) -> None:
        """Discard stale measurements after a failed service read."""
        self._report = None
        self._header.update(Text(' DEBUG  Service diagnostics unavailable', style='bold yellow'))
        self._body.update(Text(message, style='dim'))

    def update_report(self, report: DebugReport) -> None:
        """Repaints context and cache diagnostics."""
        self._report = report
        self._header.update(build_debug_header(report))
        self._body.update(build_debug_body(report))
