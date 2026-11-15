"""Multi-account model selection and persisted favourites."""

from __future__ import annotations

from pathlib import Path

from src.engine.preferences import PreferencesStore


def test_should_persist_favourites_by_account_and_model(tmp_path: Path) -> None:
    store = PreferencesStore(tmp_path / 'ui.json')
    prefs = store.load().with_favorite('account-1', 'shared/model')
    store.save(prefs)
    assert store.load().model_favorites == (('account-1', 'shared/model'),)
    assert store.load().with_favorite('account-1', 'shared/model').model_favorites == ()
