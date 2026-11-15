"""Agent model configuration is merged and validated independently."""

import asyncio
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from src.engine.mcp.config.loader import load_config


def test_agent_model_config_merges_by_name(tmp_path):
    local = tmp_path / 'local.json'
    global_config = tmp_path / 'global.json'
    global_config.write_text(
        json.dumps(
            {
                'agents': {
                    'wizard': {'account': 'first', 'model': 'old', 'effort': 'low'},
                    'scout': {'account': 'second', 'model': 'fast', 'effort': 'high'},
                }
            }
        )
    )
    local.write_text(
        json.dumps(
            {
                'agents': {
                    'wizard': {'account': 'third', 'model': 'new', 'effort': 'medium'},
                }
            }
        )
    )

    config = load_config([local, global_config], environ={})

    assert not config.errors
    assert config.agents['wizard'].account == 'third'
    assert config.agents['wizard'].model == 'new'
    assert config.agents['wizard'].effort == 'medium'
    assert config.agents['scout'].model == 'fast'


def test_invalid_agent_model_config_is_reported(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'agents': {'scout': {'account': 'a', 'model': 'm', 'effort': 'typo'}}}))

    config = load_config([path], environ={})

    assert 'scout' not in config.agents
    assert any('effort' in error for error in config.errors)


def test_composition_applies_default_only_for_new_session(tmp_path, monkeypatch):
    from src.engine.bootstrap import Composition
    from src.engine.providers.bootstrap import ProviderPaths
    from src.engine.sessions.session_store import Session
    from src.engine.settings import load_settings
    from tests.support.provider_service import _add, _service

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(tmp_path)
    config = tmp_path / '.sigil' / 'config.json'
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                'agents': {
                    'wizard': {'account': 'work', 'model': 'second/model', 'effort': 'medium'},
                }
            }
        )
    )
    service = _service(tmp_path)
    _add(service)
    monkeypatch.setattr('src.engine.bootstrap.build_service', lambda *args, **kwargs: service)

    composition = Composition(tmp_path, settings=load_settings({}), provider_paths=ProviderPaths(tmp_path / 'p'))
    assert composition.session.selection.model == 'second/model'
    assert composition.session.selection.thinking == 'medium'
    service.select('work', 'first/model', 'low')
    saved = Session(id='saved', cwd=str(tmp_path), selection=service.snapshot().selection, agent='wizard')
    restored = Composition(
        tmp_path, settings=load_settings({}), session=saved, provider_paths=ProviderPaths(tmp_path / 'p')
    )
    assert restored.session.selection.model == 'first/model'
    assert service.snapshot().thinking == 'low'


def test_invalid_project_agent_does_not_fall_back_to_global(tmp_path):
    local = tmp_path / 'local.json'
    global_config = tmp_path / 'global.json'
    local.write_text(json.dumps({'agents': {'scout': {'account': 'a', 'model': 'm', 'effort': 'wrong'}}}))
    global_config.write_text(json.dumps({'agents': {'scout': {'account': 'b', 'model': 'other', 'effort': 'high'}}}))

    config = load_config([local, global_config], environ={})

    assert 'scout' not in config.agents
    assert 'scout' in config.invalid_agents


def test_a_subagent_model_carries_the_window_its_model_reports(tmp_path, monkeypatch):
    """A configured subagent is budgeted against the window its own model knows.

    The catalogue is a network resource: a cold start, an offline machine or a model
    released after the last fetch describe nothing, and the run would then be refused
    as unbudgetable even though the model itself carries the window. The meter and the
    panel already read it from the model, so a subagent must use that same source.
    """
    from src.engine.bootstrap import Composition
    from src.engine.providers.bootstrap import ProviderPaths
    from src.engine.providers.domain.provider_kind import ProviderKind
    from src.engine.settings import load_settings
    from tests.support.provider_service import _add, _service

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    config = tmp_path / '.sigil' / 'config.json'
    config.parent.mkdir()
    config.write_text(json.dumps({'agents': {'scout': {'account': 'oai', 'model': 'gpt-5.1-codex', 'effort': 'high'}}}))
    service = _service(tmp_path)
    _add(service, alias='oai', kind=ProviderKind.OPENAI)
    monkeypatch.setattr('src.engine.bootstrap.build_service', lambda *args, **kwargs: service)

    composition = Composition(tmp_path, settings=load_settings({}), provider_paths=ProviderPaths(tmp_path / 'p'))

    # Nothing this process has fetched describes the model, which is the cold start.
    assert service.agent_capability('oai', 'gpt-5.1-codex').context_window is None

    selected = asyncio.run(composition.subagent_model('scout'))

    assert selected is not None
    assert selected.window == 400_000


@pytest.mark.parametrize('catalog_available', [True, False])
def test_should_load_the_subagent_account_catalog_before_building_its_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalog_available: bool
) -> None:
    from src.engine.bootstrap import Composition
    from src.engine.providers.bootstrap import ProviderPaths
    from src.engine.providers.catalog.catalog_service import ModelCatalogService
    from src.engine.providers.domain.provider_kind import ProviderKind
    from src.engine.settings import load_settings
    from tests.support.provider_service import _add, _service

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    config = tmp_path / '.sigil' / 'config.json'
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                'agents': {
                    'scout': {
                        'account': 'Personal',
                        'model': 'deepseek-v4.1-flash',
                        'effort': 'high',
                    }
                }
            }
        )
    )
    service = _service(tmp_path)
    _add(service, alias='root')
    _add(service, alias='Personal', kind=ProviderKind.OPENCODE)
    service.select('root', 'first/model')
    before = service.snapshot()
    requests: list[str] = []

    async def fetch(url: str, headers: Mapping[str, str], timeout: float) -> Any:
        requests.append(url)
        if not catalog_available:
            raise OSError('provider unavailable')
        return {'data': [{'id': 'deepseek-v4.1-flash', 'limit': {'context': 1_000_000}}]}

    service.catalog = ModelCatalogService(fetch_json=fetch)
    monkeypatch.setattr('src.engine.bootstrap.build_service', lambda *args, **kwargs: service)
    composition = Composition(tmp_path, settings=load_settings({}), provider_paths=ProviderPaths(tmp_path / 'p'))

    selected = asyncio.run(composition.subagent_model('scout'))

    expected_window = 1_000_000 if catalog_available else None
    assert selected is not None
    assert selected.window == expected_window
    assert selected.model.context_window == expected_window
    assert requests == ['https://opencode.ai/zen/v1/models']
    assert service.snapshot().label == before.label
    if catalog_available:
        assert asyncio.run(composition.subagent_model('scout')).window == expected_window
        assert len(requests) == 1


def test_missing_configured_account_leaves_selection_unset(tmp_path: Path) -> None:
    from src.engine.agents.model_config import AgentModelConfig
    from src.engine.bootstrap import Composition
    from src.engine.settings import load_settings

    composition = Composition(tmp_path, settings=load_settings({}))
    try:
        configured = AgentModelConfig(account='missing', model='unavailable/model', effort=None)
        assert composition._config_selection(configured) is None
        assert composition.session.selection is None
        assert composition.model_service.snapshot().is_configured is False
    finally:
        composition.close()
