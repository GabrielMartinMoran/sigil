"""Status bar: the one line under the prompt.

It carries two things, and they are different in kind:

- **Who is answering.** The active agent profile sits at the left, always
  visible. It is the one piece of context that changes how every answer reads,
  and having to open a modal to remember which agent is in effect is exactly the
  confusion the bar removes.
- **A transient question.** "Press `Esc` again to stop the agent" is read here,
  next to the prompt, without taking the eye away from it.

The model, the reasoning effort and the context usage used to live here. They
moved to the side panel, which has room for the detail.

With no transient message the transient half paints nothing: a bar that repeats
what the panel already says only spends a line of the terminal.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Static


class StatusBar(Static):
    """Agent in effect on the left, transient messages in the middle."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._agent = ''
        self._message = ''
        self._message_style = 'yellow'

    # --- the two halves ----------------------------------------------------

    def set_agent(self, name: str) -> None:
        """Paints (or clears) the active agent profile."""
        self._agent = name.strip()
        self._paint()

    def show(self, text: str, *, style: str = 'yellow') -> None:
        """Paints a transient message and keeps the agent in place."""
        self._message = text
        self._message_style = style
        self._paint()

    def clear(self) -> None:
        """Takes the transient message away, leaving the agent."""
        self._message = ''
        self._paint()

    @property
    def agent(self) -> str:
        return self._agent

    @property
    def message(self) -> str:
        return self._message

    # --- painting ----------------------------------------------------------

    def _paint(self) -> None:
        self.update(self._build())

    def _build(self) -> Text:
        text = Text()
        if self._agent:
            text.append('agent ', style='dim')
            text.append(self._agent, style='bold cyan')
        if self._message:
            if self._agent:
                text.append('  ·  ', style='dim')
            text.append(self._message, style=self._message_style)
        return text

    @property
    def text(self) -> str:
        """The bar as plain text, for tests and diagnostics."""
        return self._build().plain
