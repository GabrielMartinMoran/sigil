"""The default_path helper of the core layer."""

from __future__ import annotations

from pathlib import Path

from src.core.config.paths import sigil_home

FILE_NAME = 'ui.json'


def default_path() -> Path:
    return sigil_home() / FILE_NAME
