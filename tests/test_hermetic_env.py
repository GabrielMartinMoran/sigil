"""Tests for the configuration contract.

These guard the property that replaced the old import-time environment reads:
settings come from an explicit loader that accepts the environment as an
argument, and nothing on the provider side is read from the environment at all.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import dotenv
import pytest

from src.engine.context.compaction.constants import (
    REASONING_TARGET_STEPS,
)
from src.engine.settings import (
    DEFAULT_CONTEXT_STRATEGY,
    DEFAULT_MAX_STEPS,
    DEFAULT_MODEL_MAX_ATTEMPTS,
    SigilSettings,
    load_settings,
)

REPO = Path(__file__).resolve().parent.parent


def test_dotenv_loader_is_disabled():
    assert dotenv.load_dotenv('.env') is False


def test_defaults_are_used_when_the_environment_is_empty():
    settings = load_settings({})
    assert settings.max_steps == DEFAULT_MAX_STEPS
    assert settings.model_max_attempts == DEFAULT_MODEL_MAX_ATTEMPTS
    assert settings.context_strategy == DEFAULT_CONTEXT_STRATEGY


def test_the_loader_reads_the_mapping_it_is_given_not_the_process():
    settings = load_settings({'SIGIL_MAX_STEPS': '7', 'SIGIL_CONTEXT_STRATEGY': 'passthrough'})
    assert settings.max_steps == 7
    assert settings.context_strategy == 'passthrough'


def test_the_process_environment_is_irrelevant_when_a_mapping_is_passed(monkeypatch):
    monkeypatch.setenv('SIGIL_MAX_STEPS', '999')
    assert load_settings({}).max_steps == DEFAULT_MAX_STEPS


@pytest.mark.parametrize('value', ['0', '-1'])
def test_a_step_limit_below_one_is_rejected(value):
    with pytest.raises(ValueError):
        load_settings({'SIGIL_MAX_STEPS': value})


def test_a_non_numeric_value_fails_loudly_instead_of_falling_back():
    with pytest.raises(ValueError) as error:
        load_settings({'SIGIL_MAX_STEPS': 'many'})
    assert 'SIGIL_MAX_STEPS' in str(error.value)


def test_an_unknown_context_strategy_is_rejected():
    with pytest.raises(ValueError):
        load_settings({'SIGIL_CONTEXT_STRATEGY': 'magic'})


@pytest.mark.parametrize('raw', ['off', 'all'])
def test_removed_reasoning_modes_do_not_change_context_strategy(raw):
    import dataclasses

    from src.engine.bootstrap import build_strategy

    settings = load_settings({'SIGIL_CONTEXT_REASONING': raw, 'SIGIL_CONTEXT_KEEP_THINKING': '1'})
    assert not hasattr(settings, 'reasoning')
    strategy = build_strategy(settings)
    assert 'reasoning' not in {field.name for field in dataclasses.fields(strategy)}
    assert strategy.reasoning_steps == REASONING_TARGET_STEPS


def test_the_default_policy_keeps_the_recent_steps():
    settings = load_settings({})
    assert settings.reasoning_steps == REASONING_TARGET_STEPS


def test_the_step_count_is_its_own_number():
    assert load_settings({'SIGIL_CONTEXT_REASONING_STEPS': '9'}).reasoning_steps == 9


def test_a_step_count_below_one_is_rejected():
    with pytest.raises(ValueError) as error:
        load_settings({'SIGIL_CONTEXT_REASONING_STEPS': '0'})
    assert 'reasoning_steps' in str(error.value)


def test_settings_are_immutable():
    from dataclasses import FrozenInstanceError

    settings = SigilSettings()
    with pytest.raises(FrozenInstanceError):
        settings.max_steps = 5  # type: ignore[misc]


def test_the_sigil_home_sandbox_is_active():
    assert os.environ['SIGIL_HOME'].startswith('/')


def test_no_module_reads_the_provider_environment_at_import():
    """The regression this whole refactor exists for.

    A fresh interpreter imports the application with a hostile environment and
    must not need any provider variable, nor leave one behind as a requirement.
    """
    env = {
        'PATH': os.environ.get('PATH', ''),
        'SIGIL_HOME': os.environ['SIGIL_HOME'],
    }
    completed = subprocess.run(
        [sys.executable, '-c', 'import src.sigil; print("ok")'],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().endswith('ok')


def test_the_mcp_shutdown_timeout_is_read_by_the_loader_not_by_a_worker():
    """The grace period follows the same rule as every other setting.

    The regression: `apply_shutdown_timeout()` read `os.environ` on the MCP loading
    worker, so the value was frozen at whatever the process had at that moment —
    outside the composition root, and invisible to a test that passes a mapping.
    """
    from src.engine.mcp.shutdown import DEFAULT_SHUTDOWN_TIMEOUT, read_shutdown_timeout

    assert read_shutdown_timeout({}) == DEFAULT_SHUTDOWN_TIMEOUT
    assert read_shutdown_timeout({'SIGIL_MCP_SHUTDOWN_TIMEOUT': '1.5'}) == 1.5


def test_the_legacy_provider_module_is_gone():
    """`ConfigProvider` read the environment in its class body; it must not come back."""
    assert not (REPO / 'src' / 'config_provider.py').exists()
