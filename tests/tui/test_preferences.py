"""Preferences that outlive a session: theme and reasoning visibility."""

from __future__ import annotations

import json

from src.engine.preferences import Preferences, PreferencesStore


def test_defaults_are_the_sigil_theme_and_collapsed_reasoning():
    preferences = Preferences()
    assert preferences.theme == 'sigil'
    assert preferences.reasoning_visible is False


def test_a_missing_file_is_the_defaults(tmp_path):
    assert PreferencesStore(tmp_path / 'ui.json').load() == Preferences()


def test_preferences_round_trip(tmp_path):
    store = PreferencesStore(tmp_path / 'ui.json')
    store.save(Preferences(theme='synthwave', reasoning_visible=True))
    assert store.load() == Preferences(theme='synthwave', reasoning_visible=True)


def test_a_corrupt_file_falls_back_to_the_defaults(tmp_path):
    path = tmp_path / 'ui.json'
    path.write_text('{not json', encoding='utf-8')
    assert PreferencesStore(path).load() == Preferences()


def test_a_wrong_type_keeps_the_default_for_that_key(tmp_path):
    path = tmp_path / 'ui.json'
    path.write_text(json.dumps({'theme': 3, 'reasoning_visible': 'yes'}), encoding='utf-8')
    assert PreferencesStore(path).load() == Preferences()


def test_an_empty_theme_name_falls_back(tmp_path):
    path = tmp_path / 'ui.json'
    path.write_text(json.dumps({'theme': '   '}), encoding='utf-8')
    assert PreferencesStore(path).load().theme == 'sigil'


def test_unknown_keys_are_ignored(tmp_path):
    path = tmp_path / 'ui.json'
    path.write_text(json.dumps({'theme': 'synthwave', 'future': 1}), encoding='utf-8')
    assert PreferencesStore(path).load().theme == 'synthwave'


def test_the_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    store = PreferencesStore(tmp_path / 'ui.json')
    store.save(Preferences(theme='synthwave'))
    assert not (tmp_path / 'ui.tmp').exists()
    assert json.loads((tmp_path / 'ui.json').read_text(encoding='utf-8'))['theme'] == 'synthwave'


def test_an_unwritable_location_does_not_raise(tmp_path):
    """Preferences are a convenience; failing to store them is not worth a crash."""
    blocker = tmp_path / 'blocker'
    blocker.write_text('not a directory', encoding='utf-8')
    store = PreferencesStore(blocker / 'ui.json')
    store.save(Preferences(theme='synthwave'))
    assert store.load() == Preferences()


def test_the_updaters_do_not_mutate():
    original = Preferences()
    assert original.with_theme('synthwave').theme == 'synthwave'
    assert original.theme == 'sigil'
    assert original.with_reasoning_visible(True).reasoning_visible is True
    assert original.reasoning_visible is False
