"""Status bar: the one line under the prompt that asks for an eye right now.

The model, the reasoning effort and the context usage used to live here. They
moved to the side panel, which has room for the detail. The strip stays because
it is the bottom line of the screen: the place where a transient question
("press `Esc` again to stop the agent") is read without taking the eye away from
the prompt.

Nothing is painted by default on purpose: a bar that repeats what the panel
already says only spends a line of the terminal.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Static


class StatusBar(Static):
    """One-line strip below the prompt, empty unless something is asked."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)

    def show(self, text: str, *, style: str = 'yellow') -> None:
        """Paints a transient message."""
        self.update(Text(text, style=style))

    def clear(self) -> None:
        """Takes the message away."""
        self.update(Text(''))
