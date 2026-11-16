"""A one-line notice about links in a message."""

from __future__ import annotations

from rich.style import Style as Click
from textual.widgets import Static


class LinkNotice(Static):
    def on_click(self, event: Click) -> None:
        url = event.style.link if event.style else None
        if url and url.startswith(('https://', 'http://')):
            self.app.open_url(url)
            event.stop()
