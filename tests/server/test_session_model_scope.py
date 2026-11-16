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

from src.core.composition import Composition
from src.core.load_settings import load_settings
from src.core.providers.provider_paths import ProviderPaths
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
    monkeypatch.setattr('src.core.composition.build_service', lambda *args, **kwargs: service)
    composition = Composition(tmp_path, settings=load_settings({}), provider_paths=ProviderPaths(tmp_path / 'p'))
    return composition, service


# --- and an explicit open brings that conversation's model with it -----------


def test_switching_workspace_starts_empty_and_an_explicit_open_restores_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace switch opens a draft; the model comes back only with an explicit open.

    Switching workspace is not opening a conversation: the user names one with `/load`
    or `/api/session/open`, and the switch must not choose for them. Binding is still
    where a conversation's model is restored, in one place, for every client, so the
    conversation opened on purpose runs on its own model again.
    """
    from src.server.event_hub import EventHub
    from src.server.server_runtime import ServerRuntime

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

    from copy import copy
    from dataclasses import replace

    monkeypatch.setattr(
        'src.core.composition.build_service', lambda *args, **kwargs: replace(service, router=copy(service.router))
    )
    # Leaving and coming back opens an empty draft instead of that conversation.
    runtime.switch_workspace(str(other))
    runtime.switch_workspace(str(tmp_path))

    assert runtime.session.id != leaving.id
    assert runtime.session.messages == []

    # Opening it explicitly is what brings its own model back.
    runtime.composition.model_service.select('work', 'second/model', 'high')
    assert runtime.composition.model_service.snapshot().model == 'second/model'

    runtime.open_session(leaving.id)

    assert runtime.session.id == leaving.id
    assert runtime.composition.model_service.snapshot().model == 'first/model'
    assert runtime.session.selection.model == 'first/model'


def test_resident_compositions_keep_independent_model_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from copy import copy
    from dataclasses import replace

    composition, service = _composition(tmp_path, monkeypatch)
    # The real factory makes one service/router per composition over shared stores.
    monkeypatch.setattr(
        'src.core.composition.build_service', lambda *args, **kwargs: replace(service, router=copy(service.router))
    )
    from src.server.event_hub import EventHub
    from src.server.server_runtime import ServerRuntime

    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
    first = runtime.session_runtime
    second = runtime.resident_session(composition.store.draft_session())
    second.set_model('work', 'second/model', 'high')
    assert first.composition.model_service.snapshot().model == 'first/model'
    assert second.composition.model_service.snapshot().model == 'second/model'
    assert first.session.selection.model == 'first/model'
