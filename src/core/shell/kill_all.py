"""The kill_all helper of the core layer."""

from __future__ import annotations

from src.core.shell.track import _default


def kill_all() -> int:
    return _default.kill_all()
