"""Shell startup environment discovery and integration."""

import shutil

import pytest

from src.engine.shell import environment
from src.engine.shell.guard import SELF_GUARD_ENV
from src.engine.shell.runner import execute_command


@pytest.fixture(autouse=True)
def clear_shell_cache():
    environment.reset_cache()
    yield
    environment.reset_cache()


def test_zsh_exports_are_available_to_bash(tmp_path, monkeypatch):
    shell = shutil.which('zsh')
    if not shell:
        pytest.skip('zsh not installed')
    (tmp_path / '.zshrc').write_text(
        'echo startup-noise\nexport SIGIL_TEST_SHELL_VALUE="hello world"\nexport PATH="/shell-only:$PATH"\n'
    )
    monkeypatch.setenv('SHELL', shell)
    monkeypatch.setenv('ZDOTDIR', str(tmp_path))
    monkeypatch.delenv('SIGIL_TEST_SHELL_VALUE', raising=False)
    result = execute_command('printf "%s\\n%s" "$SIGIL_TEST_SHELL_VALUE" "$PATH"')
    assert result.returncode == 0
    assert result.stdout.splitlines()[0] == 'hello world'
    assert '/shell-only' in result.stdout.splitlines()[1].split(':')
    assert 'startup-noise' not in result.stdout


def test_missing_rc_and_missing_shell_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv('SHELL', str(tmp_path / 'missing-shell'))
    monkeypatch.setenv('SIGIL_TEST_INHERITED', 'present')
    assert execute_command('printf %s "$SIGIL_TEST_INHERITED"').stdout == 'present'


def test_uses_account_shell_when_shell_variable_is_missing(monkeypatch):
    monkeypatch.delenv('SHELL', raising=False)
    monkeypatch.setattr(environment, 'account_shell', lambda: '/account/shell')
    monkeypatch.setattr(environment, '_capture', lambda shell: {'SIGIL_ACCOUNT_SHELL': shell})
    assert execute_command('printf %s "$SIGIL_ACCOUNT_SHELL"').stdout == '/account/shell'


def test_absent_zshrc_is_not_an_error(tmp_path, monkeypatch):
    shell = shutil.which('zsh')
    if not shell:
        pytest.skip('zsh not installed')
    monkeypatch.setenv('SHELL', shell)
    monkeypatch.setenv('ZDOTDIR', str(tmp_path))
    assert execute_command('printf ok').stdout == 'ok'


def test_inherited_values_and_guard_take_precedence(monkeypatch):
    monkeypatch.setattr(
        environment,
        'shell_environment',
        lambda: {'SIGIL_TEST_VALUE': 'shell', 'PATH': '/shell/bin', SELF_GUARD_ENV: '0', 'CI': '0'},
    )
    monkeypatch.setenv('SIGIL_TEST_VALUE', 'inherited')
    result = execute_command(f'printf "%s %s %s" "$SIGIL_TEST_VALUE" "${SELF_GUARD_ENV}" "$CI"')
    assert result.stdout == 'inherited 1 1'


def test_capture_is_shared_between_commands(monkeypatch):
    calls = []
    monkeypatch.setenv('SHELL', '/test/shell')
    monkeypatch.setattr(environment, '_capture', lambda shell: calls.append(shell) or {'SIGIL_CACHED': 'yes'})
    assert execute_command('printf %s "$SIGIL_CACHED"').stdout == 'yes'
    assert execute_command('printf %s "$SIGIL_CACHED"').stdout == 'yes'
    assert calls == ['/test/shell']


def test_merge_path_deduplicates_and_keeps_inherited_first():
    assert environment.merge_path('/a:/b', '/b:/c') == '/a:/b:/c'


def test_timeout_falls_back_and_cleans_up(tmp_path, monkeypatch):
    shell = tmp_path / 'shell'
    shell.write_text('#!/bin/sh\nsleep 30 &\nwait\n')
    shell.chmod(0o755)
    monkeypatch.setenv('SHELL', str(shell))
    monkeypatch.setattr(environment, 'STARTUP_TIMEOUT', 0.1)
    assert execute_command('printf ok').stdout == 'ok'


def test_fish_config_exports_if_available(tmp_path, monkeypatch):
    shell = shutil.which('fish')
    if not shell:
        pytest.skip('fish not installed')
    config = tmp_path / 'fish'
    config.mkdir()
    (config / 'config.fish').write_text('set -gx SIGIL_TEST_FISH fish-value\n')
    monkeypatch.setenv('SHELL', shell)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    monkeypatch.delenv('SIGIL_TEST_FISH', raising=False)
    assert execute_command('printf %s "$SIGIL_TEST_FISH"').stdout == 'fish-value'
