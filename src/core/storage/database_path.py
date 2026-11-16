"""The database_path function of the storage layer."""

from __future__ import annotations

from pathlib import Path

DEFAULT_DATABASE_NAME = 'sigil.db'


def database_path(base_dir: str | Path | None = None) -> Path:
    """``<base>/sigil.db``, with the base resolved by the caller."""
    from src.core.config.paths import sigil_home

    base = Path(base_dir) if base_dir is not None else sigil_home()
    return base / DEFAULT_DATABASE_NAME
