"""Ownership and cancellation of in-flight shell process groups."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable

from src.engine.shell.process import kill_process_group


class RunningCommands:
    """Own commands and serialize process admission with cancellation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen[bytes]] = set()
        self._generation = 0

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def spawn(self, generation: int, create: Callable[[], subprocess.Popen[bytes]]) -> subprocess.Popen[bytes] | None:
        with self._lock:
            if generation != self._generation:
                return None
            process = create()
            self._processes.add(process)
            return process

    def track(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._processes.add(process)

    def forget(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            self._processes.discard(process)

    def kill_all(self) -> int:
        with self._lock:
            self._generation += 1
            processes = list(self._processes)
            self._processes.clear()
            for process in processes:
                kill_process_group(process)
            return len(processes)


_default = RunningCommands()


def track(process: subprocess.Popen[bytes]) -> None:
    _default.track(process)


def forget(process: subprocess.Popen[bytes]) -> None:
    _default.forget(process)


def kill_all() -> int:
    return _default.kill_all()
