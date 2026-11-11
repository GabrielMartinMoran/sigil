"""User interface preferences that outlive a session.

`SigilSettings` describes how a *session* runs (step limits, context strategy) and
is read from the environment once. These are different: the theme and whether the
reasoning is expanded are things the user changes while using the app, and they
should still be there the next morning. They live in ``~/.sigil/ui.json``.

The file is written atomically and every read is best-effort: a hand-edited file
that no longer parses falls back to the defaults instead of blocking startup, and
a preference the app does not know is ignored.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path

from src.engine.paths import sigil_home
from src.engine.themes import DEFAULT_THEME_NAME

FILE_NAME = 'ui.json'

THEME_KEY = 'theme'
REASONING_KEY = 'reasoning_visible'
TOOLS_KEY = 'tools_expanded'
DESKTOP_NOTIFICATIONS_KEY = 'desktop_notifications'
MODEL_FAVORITES_KEY = 'model_favorites'

DEFAULT_THEME = DEFAULT_THEME_NAME
"""Theme applied on a cold start. It is a builtin, so it always exists."""

DEFAULT_REASONING_VISIBLE = False
"""Reasoning is collapsed until the user asks to read it."""

DEFAULT_TOOLS_EXPANDED = False
"""Tool output is collapsed too: the row already summarizes what came back.

Three parallel calls used to print a dozen lines of `returncode=0 stdout=...`
between a question and its answer. What a reader needs is on the row; the output is
one command away (`/tools expanded`), and a failure shows its detail regardless.
"""


@dataclass(frozen=True, slots=True)
class Preferences:
    """What the UI remembers between runs."""

    theme: str = DEFAULT_THEME
    reasoning_visible: bool = DEFAULT_REASONING_VISIBLE
    tools_expanded: bool = DEFAULT_TOOLS_EXPANDED
    desktop_notifications: bool = True
    model_favorites: tuple[tuple[str, str], ...] = ()

    def with_theme(self, theme: str) -> Preferences:
        return replace(self, theme=theme)

    def with_reasoning_visible(self, visible: bool) -> Preferences:
        return replace(self, reasoning_visible=visible)

    def with_tools_expanded(self, expanded: bool) -> Preferences:
        return replace(self, tools_expanded=expanded)

    def with_desktop_notifications(self, enabled: bool) -> Preferences:
        return replace(self, desktop_notifications=enabled)

    def with_favorites(self, favorites: tuple[tuple[str, str], ...]) -> Preferences:
        return replace(self, model_favorites=favorites)

    def with_favorite(self, account_id: str, model: str) -> Preferences:
        key = (account_id, model)
        favorites = list(self.model_favorites)
        if key in favorites:
            favorites.remove(key)
        else:
            favorites.append(key)
        return self.with_favorites(tuple(favorites))

    def to_dict(self) -> dict[str, object]:
        return {
            THEME_KEY: self.theme,
            REASONING_KEY: self.reasoning_visible,
            TOOLS_KEY: self.tools_expanded,
            DESKTOP_NOTIFICATIONS_KEY: self.desktop_notifications,
            MODEL_FAVORITES_KEY: [list(item) for item in self.model_favorites],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Preferences:
        """Reads the known keys, keeping the default for the ones that are wrong.

        A preference with the wrong type is not worth an error dialog: the theme
        falls back to the builtin and the panel opens the way it always did.
        """
        theme = data.get(THEME_KEY)
        reasoning = data.get(REASONING_KEY)
        tools = data.get(TOOLS_KEY)
        desktop = data.get(DESKTOP_NOTIFICATIONS_KEY)
        raw_favorites = data.get(MODEL_FAVORITES_KEY)
        favorites = (
            tuple(
                dict.fromkeys(
                    (item[0], item[1])
                    for item in raw_favorites
                    if isinstance(item, list)
                    and len(item) == 2
                    and all(isinstance(value, str) and value for value in item)
                )
            )
            if isinstance(raw_favorites, list)
            else ()
        )
        return cls(
            theme=theme if isinstance(theme, str) and theme.strip() else DEFAULT_THEME,
            reasoning_visible=reasoning if isinstance(reasoning, bool) else DEFAULT_REASONING_VISIBLE,
            tools_expanded=tools if isinstance(tools, bool) else DEFAULT_TOOLS_EXPANDED,
            desktop_notifications=desktop if isinstance(desktop, bool) else True,
            model_favorites=favorites,
        )


def default_path() -> Path:
    return sigil_home() / FILE_NAME


class PreferencesStore:
    """Reads and writes ``ui.json``."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else default_path()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> Preferences:
        """The stored preferences, or the defaults."""
        try:
            raw = self._path.read_text(encoding='utf-8')
        except OSError:
            return Preferences()
        try:
            data = json.loads(raw)
        except ValueError:
            return Preferences()
        if not isinstance(data, dict):
            return Preferences()
        return Preferences.from_dict(data)

    def save(self, preferences: Preferences) -> None:
        """Persists the preferences. A failure is silent: it is not worth a turn.

        The file is written to a sibling ``.tmp`` and moved into place, so a crash
        mid-write leaves the previous preferences intact.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix('.tmp')
            temporary.write_text(json.dumps(preferences.to_dict(), indent=2) + '\n', encoding='utf-8')
            os.replace(temporary, self._path)
        except OSError:
            return
