"""Status bar: a reserved strip under the prompt, with no information in it.

The model, the reasoning effort and the context usage used to live here. They
moved to the side panel, which has room for the detail. The strip stays in the
layout so the session keeps the same height and so the slot exists when the
bottom panel gets its replacement.

Nothing is painted on purpose: a bar that repeats what the panel already says
only spends a line of the terminal.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Static


class StatusBar(Static):
    """Empty strip below the prompt."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
