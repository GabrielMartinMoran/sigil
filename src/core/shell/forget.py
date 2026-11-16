"""The forget helper of the core layer."""

from __future__ import annotations

import subprocess

from src.core.shell.track import _default


def forget(process: subprocess.Popen[bytes]) -> None:
    _default.forget(process)
