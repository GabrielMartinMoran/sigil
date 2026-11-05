"""Running Sigil from another project's directory.

The workspace Sigil talks about is the directory the process was started in, while
everything it needs to *run* lives next to its own code: the base prompt, the built-in
agent profiles, the themes. This file pins both halves of that split, because
confusing them is how a launch from a real project ends up talking about the Sigil
checkout, or crashes on a missing file.

Three things are checked:

- **The launcher.** `./sigil` resolves its own directory, so it works from anywhere and
  through a symlink, and it does not move the process: the workspace stays the caller's.
- **What is project-scoped.** Agents, skills, MCP config and the session bucket come
  from the working directory.
- **What is install-scoped.** The shipped agents and the base prompt are found from the
  package, so a project with no `.sigil/agents` still has `wizard` and `scout`, and
  never silently loses its prompt.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
LAUNCHER = PROJECT / 'sigil'


# --- the launcher -------------------------------------------------------------

FAKE_UV = """#!/usr/bin/env bash
for argument in "$@"; do
    echo "ARG:$argument"
done
echo "PWD:$(pwd)"
"""


@pytest.fixture()
def foreign_project(tmp_path: Path) -> Path:
    """A project that is not Sigil, with something in it worth being a workspace."""
    root = tmp_path / 'otherproject'
    (root / 'src').mkdir(parents=True)
    (root / 'src' / 'thing.py').write_text('def hello():\n    return 1\n', encoding='utf-8')
    (root / '.env').write_text('SIGIL_MAX_STEPS=not-a-number\n', encoding='utf-8')
    return root


@pytest.fixture()
def fake_uv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A `uv` on PATH that records what it was asked to do instead of running Sigil."""
    binaries = tmp_path / 'fakebin'
    binaries.mkdir()
    uv = binaries / 'uv'
    uv.write_text(FAKE_UV, encoding='utf-8')
    uv.chmod(0o755)
    monkeypatch.setenv('PATH', f'{binaries}{os.pathsep}{os.environ["PATH"]}')
    return uv


def _run_launcher(launcher: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ['bash', str(launcher)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_the_launcher_runs_the_project_it_belongs_to_and_stays_put(foreign_project: Path, fake_uv: Path) -> None:
    result = _run_launcher(LAUNCHER, foreign_project)

    assert result.returncode == 0, result.stderr
    # The environment is the install's; the process directory is the caller's.
    assert 'ARG:--project' in result.stdout
    assert f'ARG:{PROJECT}' in result.stdout
    assert f'ARG:{PROJECT / "main.py"}' in result.stdout
    assert f'PWD:{foreign_project}' in result.stdout
    # ...and nothing was written into the project being worked on.
    assert sorted(path.name for path in foreign_project.iterdir()) == ['.env', 'src']


def test_the_launcher_forwards_arguments(foreign_project: Path, fake_uv: Path) -> None:
    result = subprocess.run(
        ['bash', str(LAUNCHER), '--help', 'extra'],
        cwd=str(foreign_project),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert 'ARG:--help' in result.stdout
    assert 'ARG:extra' in result.stdout


def test_the_launcher_works_through_a_symlink(tmp_path: Path, foreign_project: Path, fake_uv: Path) -> None:
    """A launcher on PATH is a symlink, and it still has to find its own project."""
    binaries = tmp_path / 'localbin'
    binaries.mkdir()
    linked = binaries / 'sigil'
    linked.symlink_to(LAUNCHER)

    result = _run_launcher(linked, foreign_project)

    assert result.returncode == 0, result.stderr
    assert f'ARG:{PROJECT}' in result.stdout
    assert f'PWD:{foreign_project}' in result.stdout


def test_the_launcher_says_what_is_missing_when_uv_is_absent(
    tmp_path: Path, foreign_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PATH with the shell tools but no `uv`: the message has to be actionable."""
    binaries = tmp_path / 'barebin'
    binaries.mkdir()
    for name in ('bash', 'dirname', 'readlink'):
        found = shutil.which(name)
        assert found is not None, name
        (binaries / name).symlink_to(found)
    monkeypatch.setenv('PATH', str(binaries))

    result = _run_launcher(LAUNCHER, foreign_project)

    assert result.returncode == 127
    assert 'uv' in result.stderr
    assert 'https://docs.astral.sh/uv/' in result.stderr


# --- what is project-scoped, and what is install-scoped -----------------------


def test_the_shipped_agents_come_from_the_package_not_from_the_workspace(
    foreign_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A project with no `.sigil/agents` still gets the built-ins."""
    monkeypatch.chdir(foreign_project)
    from src.engine.agents.profile.discovery import BUILTIN_DIRNAME, default_roots
    from src.engine.agents.profile.registry import AgentRegistry

    roots = default_roots()
    assert roots[0] == ('project', foreign_project / '.sigil' / 'agents')
    assert roots[-1] == ('builtin', BUILTIN_DIRNAME)
    assert BUILTIN_DIRNAME.is_dir()
    # The built-in directory is anchored to the package, not to the process.
    assert BUILTIN_DIRNAME == (PROJECT / 'src' / 'engine' / 'agents' / 'profile' / 'builtin')

    registry = AgentRegistry(
        [
            ('project', foreign_project / '.sigil' / 'agents'),
            ('builtin', BUILTIN_DIRNAME),
        ],
        active=None,
    )
    assert registry.errors == []
    assert {'wizard', 'scout'} <= {profile.name for profile in registry.list()}
    assert registry.active_name == 'wizard'


def test_a_project_agent_shadows_a_shipped_one(foreign_project: Path) -> None:
    from src.engine.agents.profile.discovery import BUILTIN_DIRNAME
    from src.engine.agents.profile.registry import AgentRegistry

    agents = foreign_project / '.sigil' / 'agents'
    agents.mkdir(parents=True)
    (agents / 'wizard.md').write_text(
        '---\nname: wizard\ndescription: the project version\n---\nProject wizard.\n',
        encoding='utf-8',
    )

    registry = AgentRegistry([('project', agents), ('builtin', BUILTIN_DIRNAME)], active='wizard')

    assert registry.require('wizard').description == 'the project version'
    assert registry.require('wizard').source == 'project'


def test_the_base_prompt_is_read_from_the_package(foreign_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The one file every turn depends on is anchored to the module."""
    monkeypatch.chdir(foreign_project)
    from src.engine.agents import agent as agent_module

    assert Path(agent_module._SIGIL_PROMPT).is_file()
    assert Path(agent_module._SIGIL_PROMPT).parent == PROJECT / 'src' / 'engine' / 'agents' / 'prompts'


def test_the_project_skills_and_the_global_ones_both_resolve(
    foreign_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(foreign_project)
    from src.engine.skills.discovery import default_roots as skill_roots

    roots = dict(skill_roots())
    assert roots['project'] == foreign_project / '.agents' / 'skills'
    assert roots['global'] == Path.home() / '.agents' / 'skills'


def test_the_state_home_does_not_follow_the_workspace(
    foreign_project: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sessions belong to the user, not to the project being edited."""
    monkeypatch.chdir(foreign_project)
    monkeypatch.delenv('SIGIL_HOME', raising=False)
    from src.engine.paths import sessions_dir, sigil_home

    assert sigil_home() == Path.home() / '.sigil'
    assert sessions_dir().name == 'sessions'
    assert str(foreign_project) not in str(sessions_dir())

    home = tmp_path / 'sandbox-home'
    monkeypatch.setenv('SIGIL_HOME', str(home))
    assert sigil_home() == home


def test_a_project_env_file_is_not_read_by_sigil(foreign_project: Path, tmp_path: Path) -> None:
    """`load_dotenv()` resolves from Sigil's own code, so a foreign `.env` is inert.

    That is deliberate: a project's `.env` carries the project's secrets and settings,
    and letting it reach `SIGIL_*` would make Sigil's configuration depend on which
    repository it was started in. The rule is checked in a subprocess because the test
    suite replaces the loader globally, and a rule about the *real* loader has to be
    checked against the real thing.
    """
    # The probe lives outside the project, exactly like `src/sigil.py` does: that is
    # what makes `find_dotenv()` (module-relative) and `usecwd=True` disagree.
    probe = tmp_path / 'probe.py'
    probe.write_text(
        'import json\n'
        'import dotenv\n'
        'print(json.dumps({\n'
        "    'module_relative': dotenv.find_dotenv(),\n"
        "    'from_cwd': dotenv.find_dotenv(usecwd=True),\n"
        '}))\n',
        encoding='utf-8',
    )
    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(foreign_project),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    found = json.loads(result.stdout)
    project_env = str(foreign_project / '.env')
    assert found['from_cwd'] == project_env, 'the fixture must be discoverable at all'
    # What Sigil loads is anchored to its own code, not to the workspace.
    assert found['module_relative'] != project_env
    assert not found['module_relative'].startswith(str(foreign_project))
