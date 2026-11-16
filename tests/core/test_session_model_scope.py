"""Which model a conversation runs on, and how far a change reaches.

Two rules, and they are the reason this file exists:

- **A model belongs to the conversation.** Changing it in one session changes it
  there and nowhere else; opening another session brings that one's own model back.
- **`default_model` is the fallback, not the authority.** The agent's own model wins
  over it, and it wins over the memory of the last model used. When it is not
  configured, the last one used is what a new conversation starts on.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.bootstrap import Composition
from src.core.mcp.config.loader import load_config
from src.core.providers.provider_paths import ProviderPaths
from src.core.sessions.session_store import SessionStore
from src.core.settings import load_settings
from tests.support.provider_service import _add, _service

# --- the config block --------------------------------------------------------


def _write(root, data: dict, name: str = 'config.json'):
    path = root / '.sigil' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_default_model_is_read_with_its_effort(tmp_path: Path) -> None:
    path = _write(tmp_path, {'default_model': {'account': 'work', 'model': 'second/model', 'effort': 'high'}})

    config = load_config([path], environ={})

    assert not config.errors
    assert config.default_model is not None
    assert (config.default_model.account, config.default_model.model) == ('work', 'second/model')
    assert config.default_model.effort == 'high'


def test_the_project_default_model_wins_over_the_global_one(tmp_path: Path) -> None:
    local = _write(tmp_path, {'default_model': {'account': 'work', 'model': 'new', 'effort': 'low'}}, 'local.json')
    global_config = _write(
        tmp_path, {'default_model': {'account': 'work', 'model': 'old', 'effort': 'high'}}, 'global.json'
    )

    config = load_config([local, global_config], environ={})

    assert config.default_model.model == 'new'


def test_a_file_that_sets_no_default_leaves_the_other_one_standing(tmp_path: Path) -> None:
    local = _write(tmp_path, {'agents': {}}, 'local.json')
    global_config = _write(
        tmp_path, {'default_model': {'account': 'work', 'model': 'old', 'effort': 'high'}}, 'global.json'
    )

    config = load_config([local, global_config], environ={})

    assert config.default_model.model == 'old'


def test_a_bad_default_model_is_reported_and_falls_back_one_file(tmp_path: Path) -> None:
    local = _write(tmp_path, {'default_model': {'account': 'work', 'model': 'new'}}, 'local.json')
    global_config = _write(
        tmp_path, {'default_model': {'account': 'work', 'model': 'old', 'effort': 'high'}}, 'global.json'
    )

    config = load_config([local, global_config], environ={})

    assert any('default_model' in error and 'effort' in error for error in config.errors)
    assert config.default_model.model == 'old'


# --- the resolution chain ----------------------------------------------------


def _composition(tmp_path, monkeypatch, config: dict | None = None) -> tuple[Composition, object]:
    """A composition whose provider service is a cached fake with one account."""
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(tmp_path)
    if config is not None:
        _write(tmp_path, config)
    service = _service(tmp_path)
    _add(service)
    monkeypatch.setattr('src.core.bootstrap.build_service', lambda *args, **kwargs: service)
    composition = Composition(tmp_path, settings=load_settings({}), provider_paths=ProviderPaths(tmp_path / 'p'))
    return composition, service


def _reopen(tmp_path) -> Composition:
    return Composition(tmp_path, settings=load_settings({}), provider_paths=ProviderPaths(tmp_path / 'p'))


def test_the_agent_model_beats_the_default_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    composition, _ = _composition(
        tmp_path,
        monkeypatch,
        {
            'agents': {'wizard': {'account': 'work', 'model': 'first/model', 'effort': 'low'}},
            'default_model': {'account': 'work', 'model': 'second/model', 'effort': 'high'},
        },
    )

    assert composition.session.selection.model == 'first/model'
    assert composition.session.selection.thinking == 'low'


def test_the_default_model_beats_the_last_one_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    composition, service = _composition(
        tmp_path, monkeypatch, {'default_model': {'account': 'work', 'model': 'second/model', 'effort': 'high'}}
    )
    assert composition.session.selection.model == 'second/model'
    assert composition.session.selection.thinking == 'high'

    # Working on another model does not rewrite the default: the next conversation
    # starts where the config says, not where the last one was left.
    service.select('work', 'first/model', 'low')

    assert _reopen(tmp_path).session.selection.model == 'second/model'


def test_the_last_one_used_is_the_last_resort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    composition, service = _composition(tmp_path, monkeypatch)
    assert composition.session.selection.model == 'first/model'

    service.select('work', 'second/model', 'high')

    fresh = _reopen(tmp_path)
    assert fresh.session.selection.model == 'second/model'
    assert fresh.session.selection.thinking == 'high'


# --- the change stays in its session ----------------------------------------


def test_changing_the_model_reaches_only_the_session_it_changed_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    composition, service = _composition(tmp_path, monkeypatch)
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')

    first = store.create_session()
    first.agent = 'wizard'
    composition.bind_session(first)
    assert first.selection.model == 'first/model'
    store.save(first)

    second = store.create_session()
    second.agent = 'wizard'
    composition.bind_session(second)
    store.save(second)

    # The user picks another model while working in the second conversation.
    service.select('work', 'second/model', 'high')
    second.selection = service.snapshot().selection
    store.save(second)

    # Each conversation opens on its own model, and the first one was not touched.
    composition.bind_session(store.load(first.id))
    assert service.snapshot().model == 'first/model'
    composition.bind_session(store.load(second.id))
    assert service.snapshot().model == 'second/model'
    assert store.load(first.id).selection.model == 'first/model'


def test_a_session_without_a_model_gets_the_default_and_keeps_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    composition, _ = _composition(
        tmp_path, monkeypatch, {'default_model': {'account': 'work', 'model': 'second/model', 'effort': 'high'}}
    )
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')
    legacy = store.create_session()
    legacy.agent = 'wizard'
    assert legacy.selection is None

    composition.bind_session(legacy)

    assert legacy.selection is not None
    assert legacy.selection.model == 'second/model'
    assert legacy.selection.thinking == 'high'
    assert legacy.selection.model == 'second/model'
    assert legacy.selection.thinking == 'high'
