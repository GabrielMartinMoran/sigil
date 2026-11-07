"""The pre-commit gate: what it owns, what it leaves alone, and how it fails.

The hook is the only thing that keeps two promises at once — formatted code and a
committed bundle that matches its sources — so the tests are about the decisions,
not about git. `--files` is the seam: it lets a test describe a commit without
making one, and it is also what CI uses.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import pre_commit


def test_python_files_selects_only_python() -> None:
    files = ['src/app.py', 'web/src/App.tsx', 'README.md', 'scripts/x.py']
    assert pre_commit.python_files(files) == ['src/app.py', 'scripts/x.py']


def test_web_files_selects_the_client_sources() -> None:
    files = ['web/src/App.tsx', 'web/index.html', 'web/package.json', 'src/app.py']
    assert pre_commit.web_files(files) == ['web/src/App.tsx', 'web/index.html', 'web/package.json']


def test_web_files_ignores_the_bundle_and_dependencies() -> None:
    """The output and node_modules are not linted and are not inputs of a rebuild."""
    files = ['web/dist/assets/index.js', 'web/node_modules/x/index.js', 'web/src/App.tsx']
    assert pre_commit.web_files(files) == ['web/src/App.tsx']


def test_eslint_files_excludes_markup_and_styles() -> None:
    """prettier formats CSS and HTML; eslint cannot parse them, so it must not see them."""
    files = ['web/src/App.tsx', 'web/src/styles.css', 'web/index.html', 'web/src/main.tsx']
    assert pre_commit.eslint_files(files) == ['web/src/App.tsx', 'web/src/main.tsx']


@pytest.mark.parametrize(
    ('files', 'expected'),
    [
        ([], False),
        (['src/app.py'], False),
        (['README.md'], False),
        (['web/src/App.tsx'], True),
        (['web/package.json'], True),
        (['web/package-lock.json'], True),
        (['web/vite.config.ts'], True),
        # Only the output changed: there is nothing to rebuild it from.
        (['web/dist/assets/index.js'], False),
        (['web/dist/build.json'], False),
    ],
)
def test_sources_changed(files: list[str], expected: bool) -> None:
    assert pre_commit.sources_changed(files) is expected


def test_run_skips_everything_when_the_variable_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(pre_commit.SKIP_ALL, '1')
    report = pre_commit.run(files=['src/app.py', 'web/src/App.tsx'])
    assert report.skipped
    assert report.checked == ()


def test_run_with_nothing_staged_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (pre_commit.SKIP_ALL, pre_commit.SKIP_PY, pre_commit.SKIP_WEB, pre_commit.SKIP_BUILD):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    report = pre_commit.run(files=[])
    assert report.checked == ()
    assert report.rebuilt is None


def test_a_missing_ruff_is_reported_not_guessed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Erasing the toolchain must produce a message with the way out, not a traceback."""
    monkeypatch.setattr(pre_commit, '_find_ruff', lambda root: None)
    monkeypatch.delenv(pre_commit.SKIP_PY, raising=False)
    with pytest.raises(pre_commit.HookError, match='SKIP_PY_LINT=1'):
        pre_commit.run(root=tmp_path, files=['src/app.py'])


def test_a_missing_node_modules_is_reported_with_the_command(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(pre_commit.shutil, 'which', lambda name: '/usr/bin/npm')
    monkeypatch.delenv(pre_commit.SKIP_WEB, raising=False)
    monkeypatch.setattr(pre_commit, 'eslint_files', lambda files: files)
    with pytest.raises(pre_commit.HookError, match='npm ci'):
        pre_commit.run(root=tmp_path, files=['web/src/App.tsx'])


def test_python_only_commit_shells_out_to_ruff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The first step of the gate, end to end, with the subprocess boundary stubbed."""
    calls: list[list[str]] = []

    def fake_run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(pre_commit, '_run', fake_run)
    monkeypatch.setattr(pre_commit, '_find_ruff', lambda root: '/fake/ruff')
    monkeypatch.setattr(pre_commit, '_restage', lambda root, files: None)
    monkeypatch.delenv(pre_commit.SKIP_PY, raising=False)

    report = pre_commit.run(root=tmp_path, files=['src/app.py'])

    assert report.checked == ('src/app.py',)
    assert calls[0] == ['/fake/ruff', 'format', '--force-exclude', 'src/app.py']
    assert calls[1] == ['/fake/ruff', 'check', '--fix', '--force-exclude', 'src/app.py']


def test_a_lint_failure_says_the_files_and_the_escape_hatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def failing_run(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, 'src/app.py:3:1: E501 line too long\n', '')

    monkeypatch.setattr(pre_commit, '_run', failing_run)
    monkeypatch.setattr(pre_commit, '_find_ruff', lambda root: '/fake/ruff')
    monkeypatch.delenv(pre_commit.SKIP_PY, raising=False)

    with pytest.raises(pre_commit.HookError) as caught:
        pre_commit.run(root=tmp_path, files=['src/app.py'])
    message = str(caught.value)
    assert 'src/app.py:3:1' in message
    assert 'SKIP_PY_LINT=1' in message


@pytest.mark.skipif(os.environ.get('CI') == 'true', reason='touches the real repository state')
def test_the_repository_has_no_unformatted_python() -> None:
    """`ruff format --check` over the code Sigil owns: the hook cannot be the only guard.

    `src`, `tests` and `scripts` — not `.agents/skills`, whose examples are pinned
    to the upstream skills and are not ours to reformat.
    """
    root = Path(__file__).resolve().parents[1]
    ruff = pre_commit._find_ruff(root)
    assert ruff is not None, 'ruff must be installed as a dev dependency'
    targets = [str(root / name) for name in ('src', 'tests', 'scripts', 'main.py')]
    result = subprocess.run([ruff, 'format', '--check', *targets], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
