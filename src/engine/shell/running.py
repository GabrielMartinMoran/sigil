"""The shell commands that are in flight right now.

Aborting a turn cancels the asyncio task that awaits a command, but the command
itself runs in a worker thread and lives in its own process group: the task
disappears and the process keeps going until its own timeout. That is not what
an abort is supposed to feel like.

The shell layer registers every process it spawns here, so the abort path can
kill all of them at once instead of waiting them out.
"""

from __future__ import annotations

import subprocess
import threading

from src.engine.shell.process import kill_process_group

_RUNNING: set[subprocess.Popen[bytes]] = set()
_LOCK = threading.Lock()


def track(process: subprocess.Popen[bytes]) -> None:
    """Adds a spawned command to the live set."""
    with _LOCK:
        _RUNNING.add(process)


def forget(process: subprocess.Popen[bytes]) -> None:
    """Drops a command that already finished. Safe to call twice."""
    with _LOCK:
        _RUNNING.discard(process)


def kill_all() -> int:
    """Kills every live command and returns how many were killed.

    Their process group goes with them: a command that started children does not
    leave them behind. A process that exits between the snapshot and the signal
    is simply gone, which is the outcome that was wanted.
    """
    with _LOCK:
        processes = list(_RUNNING)
        _RUNNING.clear()
    for process in processes:
        kill_process_group(process)
    return len(processes)
