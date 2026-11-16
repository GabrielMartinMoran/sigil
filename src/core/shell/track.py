"""The track helper of the core layer."""

from __future__ import annotations

import subprocess

from src.core.shell.running_commands import RunningCommands

_default = RunningCommands()


def track(process: subprocess.Popen[bytes]) -> None:
    _default.track(process)
