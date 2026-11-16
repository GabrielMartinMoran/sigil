"""The PreferencesStore value of the core layer."""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.core.config.default_path import default_path
from src.core.config.preferences import Preferences


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
