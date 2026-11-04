"""Tests for the hermetic test environment set up in ``conftest.py``.

These guard the contract: the suite must not read the repo ``.env`` nor the
developer's shell. Without them, a future import order change could quietly
reintroduce the leak that made three tests fail for environment reasons.
"""

from __future__ import annotations

import os

import dotenv

from src.config_provider import ConfigProvider


def test_dotenv_loader_is_disabled():
    assert dotenv.load_dotenv('.env') is False


def test_config_does_not_come_from_the_repo_env_file():
    # `.env` in the repo root points the app at a real provider; the suite must
    # run on the pinned test values instead.
    assert ConfigProvider.LLM_PROVIDER == 'openrouter'
    assert ConfigProvider.LLM_MODEL == 'test/model'


def test_credentials_from_the_shell_are_not_visible():
    assert os.environ.get('OPENROUTER_API_KEY') == 'test-key'
    assert os.environ.get('OPENCODE_GO_API_KEY') == 'test-key'
    assert 'OPENCODE_SERVER_PASSWORD' not in os.environ


def test_sigil_overrides_from_the_shell_are_cleared():
    # `SIGIL_MAX_STEPS` and friends would otherwise change behavior per machine.
    assert not [name for name in os.environ if name.startswith('SIGIL_')]
    assert ConfigProvider.MAX_STEPS == 256
    assert ConfigProvider.CONTEXT_STRATEGY == 'windowed'
    assert ConfigProvider.REASONING_EFFORT == ''
