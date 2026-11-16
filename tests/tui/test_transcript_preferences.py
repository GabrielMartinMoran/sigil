"""Persist and validate the transcript window preference without a terminal."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.preferences import DEFAULT_TRANSCRIPT_WINDOW, MAX_TRANSCRIPT_WINDOW, Preferences
from src.core.preferences_store import PreferencesStore


def test_the_transcript_window_preference_falls_back_and_clamps(tmp_path: Path) -> None:
    """The window is a preference, so an edited file cannot break the attach."""

    path = tmp_path / 'ui.json'
    store = PreferencesStore(path)
    assert store.load().transcript_window == DEFAULT_TRANSCRIPT_WINDOW, 'a missing file is the default'

    store.save(Preferences().with_transcript_window(40))
    assert store.load().transcript_window == 40, 'a usable window survives the round trip'

    path.write_text(json.dumps({'transcript_window': 'lots'}), encoding='utf-8')
    assert store.load().transcript_window == DEFAULT_TRANSCRIPT_WINDOW, 'a wrong type is not worth a failure'

    path.write_text(json.dumps({'transcript_window': 10_000}), encoding='utf-8')
    assert store.load().transcript_window == MAX_TRANSCRIPT_WINDOW, 'a window the service would refuse is pulled down'
