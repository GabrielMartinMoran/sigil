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

from pydantic_ai.messages import ModelRequest, UserPromptPart

from src.engine.bootstrap import Composition
from src.engine.mcp.config.loader import load_config
from src.engine.providers.bootstrap import ProviderPaths
from src.engine.sessions.session_store import SessionStore
from src.engine.settings import load_settings
from tests.test_provider_service import _add, _service

# --- the config block --------------------------------------------------------


def _write(root, data: dict, name: str = 'config.json'):
    path = root / '.sigil' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


def test_default_model_is_read_with_its_effort(tmp_path):
    path = _write(tmp_path, {'default_model': {'account': 'work', 'model': 'second/model', 'effort': 'high'}})

    config = load_config([path], environ={})

    assert not config.errors
    assert config.default_model is not None
    assert (config.default_model.account, config.default_model.model) == ('work', 'second/model')
    assert config.default_model.effort == 'high'


def test_the_project_default_model_wins_over_the_global_one(tmp_path):
    local = _write(tmp_path, {'default_model': {'account': 'work', 'model': 'new', 'effort': 'low'}}, 'local.json')
    global_config = _write(
        tmp_path, {'default_model': {'account': 'work', 'model': 'old', 'effort': 'high'}}, 'global.json'
    )

    config = load_config([local, global_config], environ={})

    assert config.default_model.model == 'new'


def test_a_file_that_sets_no_default_leaves_the_other_one_standing(tmp_path):
    local = _write(tmp_path, {'agents': {}}, 'local.json')
    global_config = _write(
        tmp_path, {'default_model': {'account': 'work', 'model': 'old', 'effort': 'high'}}, 'global.json'
    )

    config = load_config([local, global_config], environ={})

    assert config.default_model.model == 'old'


def test_a_bad_default_model_is_reported_and_falls_back_one_file(tmp_path):
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
    monkeypatch.setattr('src.engine.bootstrap.build_service', lambda *args, **kwargs: service)
    composition = Composition(tmp_path, settings=load_settings({}), provider_paths=ProviderPaths(tmp_path / 'p'))
    return composition, service


def _reopen(tmp_path) -> Composition:
    return Composition(tmp_path, settings=load_settings({}), provider_paths=ProviderPaths(tmp_path / 'p'))


def test_the_agent_model_beats_the_default_model(tmp_path, monkeypatch):
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


def test_the_default_model_beats_the_last_one_used(tmp_path, monkeypatch):
    composition, service = _composition(
        tmp_path, monkeypatch, {'default_model': {'account': 'work', 'model': 'second/model', 'effort': 'high'}}
    )
    assert composition.session.selection.model == 'second/model'
    assert composition.session.selection.thinking == 'high'

    # Working on another model does not rewrite the default: the next conversation
    # starts where the config says, not where the last one was left.
    service.select('work', 'first/model', 'low')

    assert _reopen(tmp_path).session.selection.model == 'second/model'


def test_the_last_one_used_is_the_last_resort(tmp_path, monkeypatch):
    composition, service = _composition(tmp_path, monkeypatch)
    assert composition.session.selection.model == 'first/model'

    service.select('work', 'second/model', 'high')

    fresh = _reopen(tmp_path)
    assert fresh.session.selection.model == 'second/model'
    assert fresh.session.selection.thinking == 'high'


# --- the change stays in its session ----------------------------------------


def test_changing_the_model_reaches_only_the_session_it_changed_in(tmp_path, monkeypatch):
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


def test_a_session_without_a_model_gets_the_default_and_keeps_it(tmp_path, monkeypatch):
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


# --- and saving does not move it ---------------------------------------------


def test_persisting_a_session_does_not_copy_the_active_model_into_it(tmp_path, monkeypatch):
    """`persist` writes what the session has. It does not ask the router what to write.

    That was the leak: `persist` stamped the model currently in effect onto whatever
    row was being saved, so the next thing saved after a change carried it — and with
    it every conversation the user opened afterwards.
    """
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    from src.ui.app import SigilApp

    composition, service = _composition(tmp_path, monkeypatch)
    store = SessionStore(cwd=str(tmp_path), base_dir=tmp_path / 'sessions')

    first = store.create_session()
    first.agent = 'wizard'
    first.messages = [ModelRequest(parts=[UserPromptPart(content='the first conversation')])]
    composition.bind_session(first)
    store.save(first)

    second = store.create_session()
    second.agent = 'wizard'
    second.messages = [ModelRequest(parts=[UserPromptPart(content='the second conversation')])]
    composition.bind_session(second)
    store.save(second)

    # The user picks another model in the second conversation, and it is saved there.
    service.select('work', 'second/model', 'high')
    second.selection = service.snapshot().selection
    store.save(second)

    app = SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=first,
        model_service=service,
    )
    # The app is pointed at the first conversation while the router is on the second
    # one's model: exactly the moment the old code wrote the wrong row.
    app._session = first
    app._history = first.messages
    app.persist()

    assert store.load(first.id).selection.model == 'first/model'
    assert store.load(second.id).selection.model == 'second/model'


# --- and an explicit open brings that conversation's model with it -----------


def test_switching_workspace_starts_empty_and_an_explicit_open_restores_the_model(tmp_path, monkeypatch):
    """A workspace switch opens a draft; the model comes back only with an explicit open.

    Switching workspace is not opening a conversation: the user names one with `/load`
    or `/api/session/open`, and the switch must not choose for them. Binding is still
    where a conversation's model is restored, in one place, for every client, so the
    conversation opened on purpose runs on its own model again.
    """
    from src.server.events import EventHub
    from src.server.runtime import ServerRuntime

    other = tmp_path / 'other'
    other.mkdir()
    composition, service = _composition(tmp_path, monkeypatch)
    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)

    # The conversation this workspace keeps runs on the default, `first/model`.
    leaving = composition.store.create_session()
    leaving.agent = 'wizard'
    composition.bind_session(leaving)
    composition.store.save(leaving)
    assert leaving.selection.model == 'first/model'

    # Leaving and coming back opens an empty draft instead of that conversation.
    runtime.switch_workspace(str(other))
    runtime.switch_workspace(str(tmp_path))

    assert composition.session.id != leaving.id
    assert composition.session.messages == []

    # Opening it explicitly is what brings its own model back.
    service.select('work', 'second/model', 'high')
    assert service.snapshot().model == 'second/model'

    runtime.open_session(leaving.id)

    assert composition.session.id == leaving.id
    assert service.snapshot().model == 'first/model'
    assert composition.session.selection.model == 'first/model'
