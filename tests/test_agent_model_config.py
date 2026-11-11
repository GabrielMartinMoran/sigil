"""Agent model configuration is merged and validated independently."""

import json

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
    from tests.test_provider_service import _add, _service

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
