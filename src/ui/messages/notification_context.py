"""NotificationContext message of the terminal."""

from __future__ import annotations

from dataclasses import dataclass

from src.ui.render.window_title import compose


@dataclass(frozen=True, slots=True)
class NotificationContext:
    project: str = ''
    conversation: str = ''

    @property
    def title(self) -> str:
        return compose(self.project, self.conversation) or 'Sigil'
