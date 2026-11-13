"""The model defaults are written where the destination says, and nowhere else.

`agents.<name>` and `default_model` used to be hand-written. The TUI writes them
now, so the writer has one job to prove: it changes its own entry and leaves the
rest of the file — the MCP servers, the keys this version does not know, the other
agents — exactly as it found it.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.engine.agents.model_config import AgentModelConfig
from src.engine.agents.model_defaults import (
    ConfigScope,
    defaults_paths,
    read_defaults,
    save_default,
)
from src.engine.mcp.config.loader import load_config

PROJECT_MODEL = AgentModelConfig(account='work', model='openai/gpt-5', effort='high')


def _paths(tmp_path: Path) -> tuple[Path, Path]:
    """A project file and a user file, both inside the temporary directory."""
    return tmp_path / 'project' / 'config.json', tmp_path / 'home' / 'config.json'


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


# --- writing one entry -------------------------------------------------------


def test_should_write_the_default_model_to_the_destination(tmp_path: Path):
    project, user = _paths(tmp_path)

    errors = save_default(None, PROJECT_MODEL, ConfigScope.PROJECT, paths=(project, user))

    assert errors == ()
    assert _read(project) == {
        'default_model': {'account': 'work', 'model': 'openai/gpt-5', 'effort': 'high'},
    }
    assert not user.exists()
    assert load_config([project], environ={}).default_model == PROJECT_MODEL


def test_should_write_an_agent_entry_without_touching_the_others(tmp_path: Path):
    project, user = _paths(tmp_path)
    _write(project, {'agents': {'scout': {'account': 'a', 'model': 'fast', 'effort': 'low'}}})

    errors = save_default('wizard', PROJECT_MODEL, ConfigScope.PROJECT, paths=(project, user))

    assert errors == ()
    assert _read(project)['agents'] == {
        'scout': {'account': 'a', 'model': 'fast', 'effort': 'low'},
        'wizard': {'account': 'work', 'model': 'openai/gpt-5', 'effort': 'high'},
    }
    assert load_config([project], environ={}).agents['wizard'] == PROJECT_MODEL


def test_should_keep_what_the_writer_does_not_understand(tmp_path: Path):
    project, user = _paths(tmp_path)
    _write(
        project,
        {
            'mcp_servers': {'search': {'command': 'npx', 'args': ['-y', 'srv']}},
            'a_key_from_a_later_version': {'enabled': True},
        },
    )

    assert save_default(None, PROJECT_MODEL, ConfigScope.PROJECT, paths=(project, user)) == ()

    written = _read(project)
    assert written['mcp_servers'] == {'search': {'command': 'npx', 'args': ['-y', 'srv']}}
    assert written['a_key_from_a_later_version'] == {'enabled': True}
    assert written['default_model']['model'] == 'openai/gpt-5'


def test_should_write_to_the_user_file_when_that_is_the_destination(tmp_path: Path):
    project, user = _paths(tmp_path)

    errors = save_default('wizard', PROJECT_MODEL, ConfigScope.GLOBAL, paths=(project, user))

    assert errors == ()
    assert not project.exists()
    assert _read(user)['agents']['wizard']['account'] == 'work'


def test_should_clear_an_entry_and_drop_an_empty_section(tmp_path: Path):
    project, user = _paths(tmp_path)
    _write(
        project,
        {
            'default_model': {'account': 'work', 'model': 'openai/gpt-5', 'effort': 'high'},
            'agents': {
                'wizard': {'account': 'work', 'model': 'openai/gpt-5', 'effort': 'high'},
                'scout': {'account': 'a', 'model': 'fast', 'effort': 'low'},
            },
        },
    )

    assert save_default('wizard', None, ConfigScope.PROJECT, paths=(project, user)) == ()
    assert set(_read(project)['agents']) == {'scout'}

    assert save_default('scout', None, ConfigScope.PROJECT, paths=(project, user)) == ()
    assert 'agents' not in _read(project)

    assert save_default(None, None, ConfigScope.PROJECT, paths=(project, user)) == ()
    assert _read(project) == {}


def test_should_not_create_a_file_to_clear_an_entry_that_is_not_there(tmp_path: Path):
    project, user = _paths(tmp_path)

    assert save_default('wizard', None, ConfigScope.PROJECT, paths=(project, user)) == ()

    assert not project.exists()


def test_should_refuse_to_rewrite_a_file_that_does_not_parse(tmp_path: Path):
    project, user = _paths(tmp_path)
    project.parent.mkdir(parents=True)
    project.write_text('{ not json', encoding='utf-8')

    errors = save_default(None, PROJECT_MODEL, ConfigScope.PROJECT, paths=(project, user))

    assert errors and 'cannot read' in errors[0]
    assert project.read_text(encoding='utf-8') == '{ not json'


def test_should_refuse_to_replace_a_non_object_agents_block(tmp_path: Path):
    project, user = _paths(tmp_path)
    _write(project, {'agents': ['wizard']})

    errors = save_default('wizard', PROJECT_MODEL, ConfigScope.PROJECT, paths=(project, user))

    assert errors and 'agents' in errors[0]
    assert _read(project) == {'agents': ['wizard']}


# --- reading both files ------------------------------------------------------


def test_should_read_an_empty_state_when_no_file_exists(tmp_path: Path):
    project, user = _paths(tmp_path)

    state = read_defaults(paths=(project, user))

    assert state.errors == ()
    assert state.destinations == (ConfigScope.PROJECT, ConfigScope.GLOBAL)
    assert state.effective(None) is None
    assert state.effective('wizard') is None
    assert state.agent_names() == ()


def test_should_report_where_each_entry_comes_from(tmp_path: Path):
    project, user = _paths(tmp_path)
    _write(project, {'agents': {'wizard': {'account': 'project', 'model': 'p/m', 'effort': 'high'}}})
    _write(
        user,
        {
            'default_model': {'account': 'work', 'model': 'openai/gpt-5', 'effort': 'high'},
            'agents': {
                'wizard': {'account': 'user', 'model': 'u/m', 'effort': 'low'},
                'scout': {'account': 'a', 'model': 'fast', 'effort': 'low'},
            },
        },
    )

    state = read_defaults(paths=(project, user))

    assert state.sources('wizard') == frozenset({ConfigScope.PROJECT, ConfigScope.GLOBAL})
    assert state.effective('wizard') == AgentModelConfig('project', 'p/m', 'high')
    assert state.sources('scout') == frozenset({ConfigScope.GLOBAL})
    assert state.effective('scout') == AgentModelConfig('a', 'fast', 'low')
    assert state.effective(None) == PROJECT_MODEL
    assert state.agent_names() == ('scout', 'wizard')
    assert state.path_for(ConfigScope.GLOBAL) == user


def test_should_mark_an_invalid_project_entry_instead_of_falling_back(tmp_path: Path):
    project, user = _paths(tmp_path)
    _write(project, {'agents': {'wizard': {'account': 'a', 'model': 'm', 'effort': 'typo'}}})
    _write(user, {'agents': {'wizard': {'account': 'b', 'model': 'other', 'effort': 'high'}}})

    state = read_defaults(paths=(project, user))

    assert state.invalid('wizard') is True
    assert state.effective('wizard') is None
    assert any('effort' in error for error in state.errors)


def test_should_treat_a_single_file_as_the_only_destination(tmp_path: Path, monkeypatch):
    fixed = tmp_path / 'explicit' / 'config.json'
    monkeypatch.setenv('SIGIL_CONFIG', str(fixed))

    assert defaults_paths(tmp_path) == (fixed, fixed)

    assert save_default(None, PROJECT_MODEL, ConfigScope.GLOBAL) == ()
    assert read_defaults().destinations == (ConfigScope.GLOBAL,)
    assert load_config(environ={}).default_model == PROJECT_MODEL


# --- the composition ---------------------------------------------------------


def test_should_apply_a_saved_default_without_a_restart(tmp_path: Path, monkeypatch):
    """The write is only half the feature: the router has to read it next time."""
    from src.engine.bootstrap import Composition
    from src.engine.providers.bootstrap import ProviderPaths
    from src.engine.settings import load_settings
    from tests.test_provider_service import _add, _service

    monkeypatch.delenv('SIGIL_CONFIG', raising=False)
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    monkeypatch.chdir(tmp_path)
    service = _service(tmp_path)
    _add(service)
    monkeypatch.setattr('src.engine.bootstrap.build_service', lambda *args, **kwargs: service)
    composition = Composition(tmp_path, settings=load_settings({}), provider_paths=ProviderPaths(tmp_path / 'p'))
    config = AgentModelConfig('work', 'second/model', 'medium')

    assert composition.save_model_default('scout', config, ConfigScope.PROJECT) == ()

    assert composition.mcp_config.agents['scout'] == config
    chosen = composition.default_selection('scout')
    assert chosen is not None and chosen.model == 'second/model' and chosen.thinking == 'medium'
    assert composition.model_defaults_state().path_for(ConfigScope.PROJECT) == tmp_path / '.sigil' / 'config.json'
