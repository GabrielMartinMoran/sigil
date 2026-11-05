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

from src.engine.settings import (
    DEFAULT_CONTEXT_STRATEGY,
    DEFAULT_KEEP_THINKING_STEPS,
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


@pytest.mark.parametrize('raw', ['1', 'true', 'YES', 'on', 'all'])
def test_the_legacy_truthy_values_keep_every_reasoning_block(raw):
    """An existing `SIGIL_CONTEXT_KEEP_THINKING=1` keeps meaning "keep it all"."""
    assert load_settings({'SIGIL_CONTEXT_KEEP_THINKING': raw}).keep_thinking is None


@pytest.mark.parametrize('raw', ['0', 'false', 'no', 'off', 'none'])
def test_turning_it_off_drops_all_reasoning(raw):
    assert load_settings({'SIGIL_CONTEXT_KEEP_THINKING': raw}).keep_thinking == 0


def test_the_default_keeps_a_window_of_the_round_in_progress():
    assert load_settings({}).keep_thinking == DEFAULT_KEEP_THINKING_STEPS


@pytest.mark.parametrize('raw,expected', [('2', 2), ('8', 8), ('12', 12)])
def test_a_number_keeps_that_many_steps(raw, expected):
    assert load_settings({'SIGIL_CONTEXT_KEEP_THINKING': raw}).keep_thinking == expected


def test_a_negative_step_count_fails_loudly():
    with pytest.raises(ValueError) as error:
        load_settings({'SIGIL_CONTEXT_KEEP_THINKING': '-3'})
    assert 'SIGIL_CONTEXT_KEEP_THINKING' in str(error.value)


def test_a_bad_reasoning_policy_fails_loudly_instead_of_falling_back():
    with pytest.raises(ValueError) as error:
        load_settings({'SIGIL_CONTEXT_KEEP_THINKING': 'sometimes'})
    assert 'SIGIL_CONTEXT_KEEP_THINKING' in str(error.value)


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


def test_the_legacy_provider_module_is_gone():
    """`ConfigProvider` read the environment in its class body; it must not come back."""
    assert not (REPO / 'src' / 'config_provider.py').exists()
