"""The spawn helper of the core layer."""

from __future__ import annotations

import subprocess

from src.core.shell.environment import child_environment


def spawn(cmd: str, *, cwd: str | None, extra_env: dict[str, str] | None) -> subprocess.Popen[bytes]:
    """Spawns ``cmd`` in Bash, in its own process group and without stdin."""
    return subprocess.Popen(
        ['/bin/bash', '--noprofile', '--norc', '-c', cmd],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        cwd=cwd,
        env=child_environment(extra_env),
    )
