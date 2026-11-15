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
from src.engine.providers.bootstrap import ProviderPaths
from src.engine.sessions.session_store import SessionStore
from src.engine.settings import load_settings
from tests.support.provider_service import _add, _service

# --- the config block --------------------------------------------------------


def _write(root, data: dict, name: str = 'config.json'):
    path = root / '.sigil' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path


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
