"""Bottom bar with provider, model, reasoning effort and context."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.widgets import Static


class StatusBar(Static):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._provider = '-'
        self._model = '-'
        self._effort = '-'
        self._percent: float | None = None
        self._context_length: int | None = None

    def update_info(
        self,
        *,
        provider: str,
        model: str,
        effort: str,
        percent: float | None,
        context_length: int | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._effort = effort
        self._percent = percent
        self._context_length = context_length
        self.update(self._build())

    def _build(self) -> Text:
        text = Text()
        text.append(f' {self._provider} ', style='bold cyan')
        text.append(' · ', style='dim')
        text.append(self._model, style='bold')
        text.append(' · effort: ', style='dim')
        text.append(self._effort, style='magenta')
        text.append(' · ctx: ', style='dim')

        if self._percent is None:
            text.append('?', style='dim')
        else:
            if self._percent < 70:
                style = 'green'
            elif self._percent < 90:
                style = 'yellow'
            else:
                style = 'bold red'
            text.append(f'{self._percent:.0f}%', style=style)
            if self._context_length:
                text.append(f' / {self._context_length}', style='dim')
        return text
