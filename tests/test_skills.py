"""Tests del protocolo Agent Skills (sin red ni LLM)."""

import asyncio
import base64
import os
from pathlib import Path

import pytest

from src.engine.skills import rendering
from src.engine.skills.models.skill_not_found import SkillNotFound
from src.engine.skills.models.skill_parse_error import SkillParseError
from src.engine.skills.models.skill_resource_error import SkillResourceError
from src.engine.skills.parser import parse_skill_metadata, split_frontmatter
from src.engine.skills.registry import SkillRegistry
from src.engine.skills.tools import register_skill_tools


def write_skill(
    root: Path,
    name: str,
    *,
    description: str = 'A test skill.',
    body: str = '# Body\n\nDo the thing.',
    frontmatter: str = '',
    files: dict[str, str | bytes] | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: {description}\n{frontmatter}---\n\n{body}\n',
        encoding='utf-8',
    )
    for relative, content in (files or {}).items():
        target = skill_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding='utf-8')
    return skill_dir


class DummyAgent:
    def __init__(self) -> None:
        self.registered = {}

    def tool_plain(self, func):
        self.registered[func.__name__] = func
        return func


# --- Parser -------------------------------------------------------------------


def test_split_frontmatter_returns_body():
    data, body = split_frontmatter('---\nname: demo\ndescription: hi\n---\n\n# Title\n')
    assert data == {'name': 'demo', 'description': 'hi'}
    assert body.strip() == '# Title'


def test_split_frontmatter_handles_crlf_and_bom():
    data, body = split_frontmatter('\ufeff---\r\nname: demo\r\ndescription: hi\r\n---\r\nbody')
    assert data['name'] == 'demo'
    assert body == 'body'


def test_split_frontmatter_requires_block():
    with pytest.raises(SkillParseError):
        split_frontmatter('# no frontmatter')


def test_split_frontmatter_rejects_unterminated_block():
    with pytest.raises(SkillParseError):
        split_frontmatter('---\nname: demo\n')


def test_parse_metadata_validates_name(tmp_path):
    skill_dir = write_skill(tmp_path, 'Bad_Name')
    with pytest.raises(SkillParseError):
        parse_skill_metadata(skill_dir / 'SKILL.md', source='project')


def test_parse_metadata_requires_description(tmp_path):
    skill_dir = tmp_path / 'demo'
    skill_dir.mkdir()
    (skill_dir / 'SKILL.md').write_text('---\nname: demo\n---\nbody', encoding='utf-8')
    with pytest.raises(SkillParseError):
        parse_skill_metadata(skill_dir / 'SKILL.md', source='project')


def test_parse_metadata_keeps_unknown_keys_and_flags(tmp_path):
    skill_dir = write_skill(
        tmp_path,
        'demo',
        frontmatter='license: MIT\nallowed-tools: read write\ndisable-model-invocation: true\nversion: "1.0"\n',
    )
    metadata = parse_skill_metadata(skill_dir / 'SKILL.md', source='project')
    assert metadata.license == 'MIT'
    assert metadata.allowed_tools == ('read', 'write')
    assert metadata.model_invocable is False
    assert metadata.extra == {'version': '1.0'}
    assert any('Unknown frontmatter keys' in warning for warning in metadata.warnings)


# --- Registry -----------------------------------------------------------------


def test_registry_discovers_and_project_shadows_global(tmp_path):
    project = tmp_path / 'project'
    global_root = tmp_path / 'global'
    write_skill(project, 'shared', description='project version')
    write_skill(global_root, 'shared', description='global version')
    write_skill(global_root, 'only-global')

    registry = SkillRegistry([('project', project), ('global', global_root)])

    assert [skill.name for skill in registry.list()] == ['only-global', 'shared']
    assert registry.get('shared').description == 'project version'
    assert registry.get('shared').source == 'project'
    assert registry.get('only-global').source == 'global'


def test_registry_ignores_dirs_without_skill_md(tmp_path):
    (tmp_path / 'not-a-skill').mkdir()
    write_skill(tmp_path, 'real')
    registry = SkillRegistry([('project', tmp_path)])
    assert [skill.name for skill in registry.list()] == ['real']


def test_registry_records_invalid_skill_without_breaking_others(tmp_path):
    write_skill(tmp_path, 'good')
    bad = tmp_path / 'bad'
    bad.mkdir()
    (bad / 'SKILL.md').write_text('no frontmatter', encoding='utf-8')

    registry = SkillRegistry([('project', tmp_path)])

    assert [skill.name for skill in registry.list()] == ['good']
    assert len(registry.errors) == 1
    assert 'bad' in registry.errors[0]


def test_registry_discovers_symlinked_skill(tmp_path):
    real = tmp_path / 'real'
    write_skill(real, 'linked')
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'linked').symlink_to(real / 'linked', target_is_directory=True)

    registry = SkillRegistry([('project', root)])
    assert registry.get('linked') is not None


def test_registry_lookup_by_directory_alias(tmp_path):
    skill_dir = tmp_path / 'dir-name'
    skill_dir.mkdir()
    (skill_dir / 'SKILL.md').write_text('---\nname: canonical\ndescription: hi\n---\nbody', encoding='utf-8')
    registry = SkillRegistry([('project', tmp_path)])
    assert registry.get('canonical').name == 'canonical'
    assert registry.get('dir-name').name == 'canonical'


def test_registry_unknown_skill_raises(tmp_path):
    registry = SkillRegistry([('project', tmp_path)])
    with pytest.raises(SkillNotFound):
        registry.load('missing')


# --- Carga y recursos ---------------------------------------------------------


def test_load_returns_body_and_resources(tmp_path):
    write_skill(
        tmp_path,
        'demo',
        body='# Demo\n\nFollow these steps.',
        files={
            'scripts/run.py': 'print(1)',
            'references/guide.md': '# Guide',
            'assets/logo.txt': 'x',
            'notes.txt': 'n',
        },
    )
    registry = SkillRegistry([('project', tmp_path)])
    skill = registry.load('demo')

    assert skill.body == '# Demo\n\nFollow these steps.'
    kinds = {resource.path: resource.kind for resource in skill.resources}
    assert kinds['scripts/run.py'] == 'script'
    assert kinds['references/guide.md'] == 'reference'
    assert kinds['assets/logo.txt'] == 'asset'
    assert kinds['notes.txt'] == 'other'
    assert 'SKILL.md' not in kinds


@pytest.mark.parametrize('bad_path', ['../SKILL.md', '/etc/passwd', 'scripts/../../x', '..'])
def test_read_resource_blocks_traversal(tmp_path, bad_path):
    write_skill(tmp_path, 'demo', files={'scripts/run.py': 'print(1)'})
    registry = SkillRegistry([('project', tmp_path)])
    with pytest.raises(SkillResourceError):
        registry.read_resource('demo', bad_path)


def test_read_resource_returns_text(tmp_path):
    write_skill(tmp_path, 'demo', files={'references/guide.md': '# Guide'})
    registry = SkillRegistry([('project', tmp_path)])
    data, truncated, size = registry.read_resource('demo', 'references/guide.md')
    assert data == b'# Guide'
    assert truncated is False
    assert size == len(b'# Guide')


def test_read_resource_truncates(tmp_path):
    write_skill(tmp_path, 'demo', files={'big.txt': 'abcdef'})
    registry = SkillRegistry([('project', tmp_path)])
    data, truncated, size = registry.read_resource('demo', 'big.txt', max_bytes=3)
    assert data == b'abc'
    assert truncated is True
    assert size == 6


def test_read_resource_missing_raises(tmp_path):
    write_skill(tmp_path, 'demo')
    registry = SkillRegistry([('project', tmp_path)])
    with pytest.raises(SkillResourceError):
        registry.read_resource('demo', 'nope.txt')


# --- Tools --------------------------------------------------------------------


def test_tools_are_registered_and_list_filters(tmp_path):
    write_skill(tmp_path, 'alpha', description='Handles alpha tasks')
    write_skill(tmp_path, 'beta', description='Handles beta tasks')
    registry = SkillRegistry([('project', tmp_path)])
    agent = DummyAgent()
    register_skill_tools(agent, registry)

    assert set(agent.registered) == {'list_skills', 'load_skill', 'read_skill_resource', 'run_skill_script'}
    assert [skill.name for skill in agent.registered['list_skills']()] == ['alpha', 'beta']
    assert [skill.name for skill in agent.registered['list_skills']('beta')] == ['beta']


def test_load_skill_tool_returns_ack_and_registers(tmp_path):
    """El cuerpo no viaja en el resultado: se inyecta como bloque de instrucciones."""
    from src.engine.skills.loaded import LoadedSkills

    write_skill(tmp_path, 'demo', body='# Demo body', files={'references/a.md': 'A'})
    registry = SkillRegistry([('project', tmp_path)])
    loaded_skills = LoadedSkills()
    agent = DummyAgent()
    register_skill_tools(agent, registry, loaded=loaded_skills)

    loaded = agent.registered['load_skill']('demo')
    assert loaded.name == 'demo'
    assert not hasattr(loaded, 'body')
    assert 'Loaded skills' in loaded.note
    assert [resource.path for resource in loaded.resources] == ['references/a.md']
    assert loaded_skills.names() == ('demo',)

    # El cuerpo aparece en el bloque de instrucciones, no en el tool result.
    block = rendering.render_loaded_skills(registry, loaded_skills.names())
    assert '# Demo body' in block
    assert 'references/a.md' not in block


def test_load_skill_is_idempotent_in_the_registry(tmp_path):
    from src.engine.skills.loaded import LoadedSkills

    write_skill(tmp_path, 'demo', body='# Demo body')
    registry = SkillRegistry([('project', tmp_path)])
    loaded_skills = LoadedSkills()
    agent = DummyAgent()
    register_skill_tools(agent, registry, loaded=loaded_skills)

    agent.registered['load_skill']('demo')
    agent.registered['load_skill']('demo')
    assert loaded_skills.names() == ('demo',)


def test_render_loaded_skills_is_empty_without_names(tmp_path):
    registry = SkillRegistry([('project', tmp_path)])
    assert rendering.render_loaded_skills(registry, ()) == ''


def test_read_skill_resource_tool_decodes_binary(tmp_path):
    payload = b'\x00\x01\x02\xff'
    write_skill(tmp_path, 'demo', files={'assets/blob.bin': payload})
    registry = SkillRegistry([('project', tmp_path)])
    agent = DummyAgent()
    register_skill_tools(agent, registry)

    result = agent.registered['read_skill_resource']('demo', 'assets/blob.bin')
    assert result.binary is True
    assert result.encoding == 'base64'
    assert base64.b64decode(result.content) == payload
    assert result.size == len(payload)


def test_render_available_skills_excludes_disabled(tmp_path):
    write_skill(tmp_path, 'visible', description='Visible skill')
    write_skill(tmp_path, 'hidden', description='Hidden skill', frontmatter='disable-model-invocation: true\n')
    registry = SkillRegistry([('project', tmp_path)])

    rendered = rendering.render_available_skills(registry)
    assert '`visible`' in rendered
    assert '`hidden`' not in rendered


def test_render_available_skills_empty(tmp_path):
    assert rendering.render_available_skills(SkillRegistry([('project', tmp_path)])) == ''


# --- Ejecución de scripts -----------------------------------------------------


def test_run_python_script_uses_skill_cwd(tmp_path):
    write_skill(
        tmp_path,
        'demo',
        files={'scripts/hello.py': ("import os, sys\nprint('cwd=' + os.getcwd())\nprint('arg=' + sys.argv[1])\n")},
    )
    registry = SkillRegistry([('project', tmp_path)])
    agent = DummyAgent()
    register_skill_tools(agent, registry)

    result = asyncio.run(agent.registered['run_skill_script']('demo', 'scripts/hello.py', ['world']))

    assert result.returncode == 0
    assert f'cwd={registry.get("demo").path}' in result.stdout
    assert 'arg=world' in result.stdout


def test_run_bash_script(tmp_path):
    write_skill(tmp_path, 'demo', files={'scripts/hello.sh': 'echo "hello $1"\n'})
    registry = SkillRegistry([('project', tmp_path)])
    agent = DummyAgent()
    register_skill_tools(agent, registry)

    result = asyncio.run(agent.registered['run_skill_script']('demo', 'scripts/hello.sh', ['bash']))

    assert result.returncode == 0
    assert result.stdout.strip() == 'hello bash'


def test_run_script_reports_failure(tmp_path):
    write_skill(tmp_path, 'demo', files={'scripts/fail.py': 'import sys\nsys.exit(3)\n'})
    registry = SkillRegistry([('project', tmp_path)])
    agent = DummyAgent()
    register_skill_tools(agent, registry)

    result = asyncio.run(agent.registered['run_skill_script']('demo', 'scripts/fail.py'))
    assert result.returncode == 3


def test_run_script_blocks_outside_skill(tmp_path):
    write_skill(tmp_path, 'demo')
    registry = SkillRegistry([('project', tmp_path)])
    agent = DummyAgent()
    register_skill_tools(agent, registry)

    with pytest.raises(SkillResourceError):
        asyncio.run(agent.registered['run_skill_script']('demo', '../outside.py'))


def test_run_script_missing_raises(tmp_path):
    write_skill(tmp_path, 'demo')
    registry = SkillRegistry([('project', tmp_path)])
    agent = DummyAgent()
    register_skill_tools(agent, registry)

    with pytest.raises(SkillResourceError):
        asyncio.run(agent.registered['run_skill_script']('demo', 'scripts/nope.py'))


# --- Raíces por defecto -------------------------------------------------------


def test_default_roots_env_override(monkeypatch, tmp_path):
    from src.engine.skills.discovery import ENV_SKILLS_DIRS, default_roots

    first = tmp_path / 'one'
    second = tmp_path / 'two'
    monkeypatch.setenv(ENV_SKILLS_DIRS, os.pathsep.join([str(first), str(second)]))
    assert default_roots() == [('custom', first), ('custom', second)]


def test_script_named_main_py_is_allowed(tmp_path):
    write_skill(tmp_path, 'demo', files={'scripts/main.py': "print('main ok')\n"})
    registry = SkillRegistry([('project', tmp_path)])
    agent = DummyAgent()
    register_skill_tools(agent, registry)

    result = asyncio.run(agent.registered['run_skill_script']('demo', 'scripts/main.py'))
    assert result.returncode == 0
    assert result.stdout.strip() == 'main ok'


def test_metadata_field_is_preserved(tmp_path):
    skill_dir = write_skill(tmp_path, 'demo', frontmatter='metadata:\n  version: "2.0"\n  author: me\n')
    metadata = parse_skill_metadata(skill_dir / 'SKILL.md', source='project')
    assert metadata.metadata == {'version': '2.0', 'author': 'me'}


def test_tools_generate_valid_agent_schemas(tmp_path):
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    write_skill(tmp_path, 'demo', files={'scripts/hello.py': "print('hi')\n"})
    registry = SkillRegistry([('project', tmp_path)])
    agent = Agent(TestModel(call_tools=[]), instructions='x')
    register_skill_tools(agent, registry)

    result = asyncio.run(agent.run('hello'))
    assert result.output


def test_registry_refresh_discovers_new_skill(tmp_path):
    registry = SkillRegistry([('project', tmp_path)])
    assert registry.list() == []

    write_skill(tmp_path, 'late')
    registry.refresh()

    assert [skill.name for skill in registry.list()] == ['late']


def test_list_skills_tool_refreshes(tmp_path):
    registry = SkillRegistry([('project', tmp_path)])
    agent = DummyAgent()
    register_skill_tools(agent, registry)

    assert agent.registered['list_skills']() == []
    write_skill(tmp_path, 'late')
    assert [skill.name for skill in agent.registered['list_skills']()] == ['late']
