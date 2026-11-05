"""Tests for the agent profiles: frontmatter, discovery and prompt.

Three layers are pinned here, in the order they fail in real use:

- **Parsing.** A profile is a file a user writes by hand, so a bad key or a
  missing name has to produce a message, not a traceback and not a silently
  unrestricted agent. The `permissions` block has its own module:
  ``test_agent_permissions.py``.
- **Discovery.** Project shadows global, global shadows built-in, and a broken
  file does not take the rest of the set down with it.
- **The prompt.** The body reaches the instructions after the base prompt, and
  the block is rewritten rather than appended: switching agent has to *remove*
  the previous profile's instructions.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.engine.agents.agent import create_agent
from src.engine.agents.profile.models.agent_parse_error import AgentParseError
from src.engine.agents.profile.parser import build_profile, parse_profile
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.profile.rendering import render_agent_profile

FRONTMATTER = '---\nname: {name}\ndescription: {description}\n{extra}---\n'


def write_profile(
    root: Path,
    name: str,
    *,
    description: str | None = None,
    body: str = 'Be nice.',
    extra: str = '',
    filename: str | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / (filename or f'{name}.md')
    path.write_text(
        FRONTMATTER.format(name=name, description=description or f'Profile {name}.', extra=extra) + body,
        encoding='utf-8',
    )
    return path


# --- parsing -----------------------------------------------------------------


def test_parse_reads_the_metadata_and_the_body(tmp_path):
    profile_file = write_profile(tmp_path, 'reviewer', description='Reviews code.', body='# Rules\nBe strict.')

    profile, body = parse_profile(profile_file, source='project')

    assert profile.name == 'reviewer'
    assert profile.description == 'Reviews code.'
    assert profile.source == 'project'
    assert profile.label == 'Reviewer'
    assert body == '# Rules\nBe strict.'


def test_parse_requires_name_and_description(tmp_path):
    missing_name = tmp_path / 'a.md'
    missing_name.write_text('---\ndescription: hi\n---\nbody', encoding='utf-8')
    with pytest.raises(AgentParseError, match='`name`'):
        parse_profile(missing_name, source='project')

    missing_description = tmp_path / 'b.md'
    missing_description.write_text('---\nname: demo\n---\nbody', encoding='utf-8')
    with pytest.raises(AgentParseError, match='`description`'):
        parse_profile(missing_description, source='project')


def test_parse_rejects_a_name_that_is_not_kebab_case(tmp_path):
    profile_file = write_profile(tmp_path, 'Bad_Name')
    with pytest.raises(AgentParseError, match='kebab-case'):
        parse_profile(profile_file, source='project')


def test_parse_rejects_unknown_frontmatter_keys(tmp_path):
    profile_file = write_profile(tmp_path, 'demo', extra='tools: [run]\n')
    with pytest.raises(AgentParseError, match='tools'):
        parse_profile(profile_file, source='project')


def test_parse_requires_a_frontmatter_block(tmp_path):
    path = tmp_path / 'raw.md'
    path.write_text('just text', encoding='utf-8')
    with pytest.raises(AgentParseError, match='frontmatter'):
        parse_profile(path, source='project')


def test_parse_accepts_crlf(tmp_path):
    path = tmp_path / 'demo.md'
    path.write_text('---\r\nname: demo\r\ndescription: hi\r\n---\r\nbody', encoding='utf-8')
    profile, body = parse_profile(path, source='project')
    assert profile.name == 'demo'
    assert body == 'body'


def test_build_profile_reports_a_too_long_description():
    with pytest.raises(AgentParseError, match='too long'):
        build_profile(
            {'name': 'demo', 'description': 'x' * 2000},
            profile_file=Path('/tmp/demo.md'),
            source='project',
        )


# --- discovery ---------------------------------------------------------------


def test_registry_lists_the_builtins():
    registry = AgentRegistry(active=None)
    names = [profile.name for profile in registry.list()]
    assert 'wizard' in names
    assert registry.get('wizard').source == 'builtin'


def test_project_shadows_global_and_builtin(tmp_path):
    project = tmp_path / 'project'
    glob = tmp_path / 'global'
    write_profile(project, 'wizard', description='project version')
    write_profile(glob, 'wizard', description='global version')
    write_profile(glob, 'helper')

    registry = AgentRegistry([('project', project), ('global', glob), ('builtin', tmp_path / 'none')])

    assert registry.get('wizard').description == 'project version'
    assert registry.get('wizard').source == 'project'
    assert registry.get('helper').source == 'global'


def test_registry_records_a_broken_profile_without_losing_the_others(tmp_path):
    write_profile(tmp_path, 'good')
    (tmp_path / 'bad.md').write_text('no frontmatter', encoding='utf-8')

    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')])

    assert [profile.name for profile in registry.list()] == ['good']
    assert len(registry.errors) == 1
    assert 'bad.md' in registry.errors[0]


def test_registry_skips_notes_and_hidden_files(tmp_path):
    write_profile(tmp_path, 'real')
    (tmp_path / '_notes.md').write_text('scratch', encoding='utf-8')
    (tmp_path / '.hidden.md').write_text('scratch', encoding='utf-8')
    (tmp_path / 'README.txt').write_text('scratch', encoding='utf-8')

    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')])
    assert [profile.name for profile in registry.list()] == ['real']


def test_active_falls_back_when_the_remembered_name_is_gone(tmp_path):
    write_profile(tmp_path, 'present')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='deleted')
    assert registry.active_name == 'present'


def test_activate_rejects_an_unknown_name(tmp_path):
    from src.engine.agents.profile.models.agent_not_found import AgentNotFound

    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='present')
    with pytest.raises(AgentNotFound):
        registry.activate('nope')


def test_refresh_keeps_the_active_profile(tmp_path):
    write_profile(tmp_path, 'one')
    write_profile(tmp_path, 'two')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')])
    registry.activate('two')
    registry.refresh()
    assert registry.active_name == 'two'


# --- the prompt block --------------------------------------------------------


def test_the_body_reaches_the_instructions(tmp_path):
    write_profile(tmp_path, 'wizard', body='You are a plain wizard.')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='wizard')
    agent = create_agent(None, agent_registry=registry)

    with agent.override(model=_TestModel()):
        result = asyncio.run(agent.run('hello'))

    instructions = result.all_messages()[0].instructions or ''
    assert 'You are a plain wizard.' in instructions
    assert 'Active agent: Wizard' in instructions


def test_the_profile_goes_after_the_base_prompt(tmp_path):
    write_profile(tmp_path, 'wizard', body='PROFILE BODY')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='wizard')
    agent = create_agent(None, agent_registry=registry)

    with agent.override(model=_TestModel()):
        result = asyncio.run(agent.run('hello'))

    instructions = result.all_messages()[0].instructions or ''
    assert instructions.index('You are **Sigil**') < instructions.index('PROFILE BODY')


def test_switching_agent_replaces_the_previous_block(tmp_path):
    write_profile(tmp_path, 'first', body='FIRST BODY')
    write_profile(tmp_path, 'second', body='SECOND BODY')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='first')
    agent = create_agent(None, agent_registry=registry)

    with agent.override(model=_TestModel()):
        before = asyncio.run(agent.run('one')).all_messages()[0].instructions or ''
        registry.activate('second')
        after = asyncio.run(agent.run('two')).all_messages()[0].instructions or ''

    assert 'FIRST BODY' in before
    assert 'SECOND BODY' in after
    # Rewritten, not appended: the old profile must not travel along.
    assert 'FIRST BODY' not in after


def test_a_profile_with_no_installed_set_contributes_nothing(tmp_path):
    registry = AgentRegistry([('project', tmp_path)], active=None)
    assert registry.active is None
    assert render_agent_profile(registry) == ''


def test_an_empty_body_is_not_a_block(tmp_path):
    write_profile(tmp_path, 'empty', body='')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='empty')
    assert render_agent_profile(registry) == ''


# --- the built-in set --------------------------------------------------------


def test_the_shipped_wizard_parses():
    registry = AgentRegistry([('builtin', _builtin_root())], active='wizard')
    profile = registry.require('wizard')
    assert profile.source == 'builtin'
    assert 'Wizard' in registry.active_body()


def _builtin_root() -> Path:
    from src.engine.agents.profile.discovery import BUILTIN_DIRNAME

    return BUILTIN_DIRNAME


def _TestModel():
    from pydantic_ai.models.test import TestModel

    return TestModel()
