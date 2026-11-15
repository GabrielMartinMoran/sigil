"""Installing the hooks: once per clone, silently, and never in the wrong repo.

`core.hooksPath` is per-clone configuration, so a clone starts without the gate.
Two entry points close that gap — the launcher and `npm ci` — and both delegate to
this module. What is tested here is the contract those entry points rely on:
`ensure()` is idempotent, never raises, respects the opt-out, and installs into
Sigil's checkout rather than into the workspace the user opened.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import install_hooks


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(['git', *args], cwd=root, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A repository shaped like Sigil's: a checkout with a tracked hooks directory."""
    root = tmp_path / 'sigil'
    (root / '.githooks').mkdir(parents=True)
    hook = root / '.githooks' / 'pre-commit'
    hook.write_text('#!/usr/bin/env bash\nexit 0\n', encoding='utf-8')
    _git(root, 'init', '-q')
    return root


def test_a_fresh_clone_has_no_hooks(clone: Path) -> None:
    assert not install_hooks.is_installed(clone)


def test_ensure_installs_into_a_fresh_clone(clone: Path) -> None:
    assert install_hooks.ensure(clone) is True
    assert install_hooks.is_installed(clone)


def test_ensure_is_idempotent(clone: Path) -> None:
    """The launcher calls this on every start: the second call must be a no-op."""
    assert install_hooks.ensure(clone) is True
    assert install_hooks.ensure(clone) is False
    assert install_hooks.is_installed(clone)


def test_ensure_respects_the_opt_out(clone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(install_hooks.OPT_OUT, '1')
    assert install_hooks.ensure(clone) is False
    assert not install_hooks.is_installed(clone)


def test_ensure_leaves_a_directory_that_is_not_a_checkout(tmp_path: Path) -> None:
    """Exported source, an unpacked tarball, a wheel: nothing to configure."""
    plain = tmp_path / 'plain'
    (plain / '.githooks').mkdir(parents=True)
    assert install_hooks.ensure(plain) is False


def test_ensure_does_not_raise_when_git_refuses(clone: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A read-only config must not stop Sigil from starting."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise subprocess.CalledProcessError(1, 'git')

    monkeypatch.setattr(install_hooks.subprocess, 'run', refuse)
    assert install_hooks.ensure(clone) is False


def test_project_root_is_the_checkout_not_the_workspace() -> None:
    """A user opens Sigil in their own repo: the hooks belong to Sigil's, not to theirs."""
    root = install_hooks.project_root()
    assert (root / 'scripts' / 'install_hooks.py').is_file()
    assert (root / '.githooks' / 'pre-commit').is_file()


def test_install_makes_the_hook_executable(clone: Path) -> None:
    hook = clone / '.githooks' / 'pre-commit'
    hook.chmod(0o644)
    install_hooks.install(clone)
    assert hook.stat().st_mode & 0o100


def test_a_previous_hooks_path_is_reported(clone: Path) -> None:
    """The message names what it replaced, because the user may have set it on purpose."""
    _git(clone, 'config', 'core.hooksPath', '/somewhere/else')
    messages = install_hooks.install(clone)
    assert any('core.hooksPath' in line and '/somewhere/else' in line for line in messages)
    assert install_hooks.is_installed(clone)


def test_quiet_mode_says_nothing_when_already_installed(clone: Path, capsys: pytest.CaptureFixture[str]) -> None:
    install_hooks.install(clone)
    capsys.readouterr()
    assert install_hooks._main(['--root', str(clone), '--quiet']) == 0
    assert capsys.readouterr().out == ''
