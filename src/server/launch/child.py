"""A server process the launcher started, and the output it kept."""

from __future__ import annotations

import os
import subprocess
from typing import BinaryIO

OUTPUT_TAIL_BYTES = 2048
"""How much of a dead child's output is quoted in the error it caused."""

TERMINATE_TIMEOUT = 5.0
"""Seconds between a polite `terminate` and a `kill` for a child this module owns."""


class Child:
    """A server process this launcher started, and the output it kept.

    The process runs in its own session, so closing the terminal that started it
    does not take the service with it — that is the product decision the lock and
    the idle shutdown, not the terminal, are responsible for. Its standard output
    and error go to an anonymous temporary file: unbounded, because a pipe would
    block the child once it filled; invisible, because a banner drawn over a
    running interface is a broken interface; and readable, because a process that
    dies without publishing should quote what it said.

    Death is noticed with ``poll``: the kernel reports it as soon as it happens and
    the exited child is reaped by that call, so a launcher that watched a child die
    leaves no zombie behind.
    """

    def __init__(self, process: subprocess.Popen[bytes], output: BinaryIO) -> None:
        self._process = process
        self._output = output

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def exited(self) -> bool:
        """Whether the process is gone, reaping it as a side effect."""

        return self._process.poll() is not None

    def exit_code(self) -> int | None:
        """The exit code, or ``None`` while the process is still running."""

        return self._process.poll()

    def wait(self, *, timeout: float | None = None) -> int:
        """Blocks until the process exits, or raises ``subprocess.TimeoutExpired``."""

        return self._process.wait(timeout=timeout)

    def output_tail(self, limit: int = OUTPUT_TAIL_BYTES) -> str:
        """The last ``limit`` bytes the child wrote, decoded leniently.

        Only ever used to explain a failure, so a child that wrote nothing, or a
        file already closed, answers ``''`` instead of raising: the error that is
        being built is the message the caller needs, not this one.
        """

        try:
            self._output.flush()
            size = self._output.seek(0, os.SEEK_END)
            self._output.seek(max(0, size - limit))
            return self._output.read(limit).decode('utf-8', 'replace').strip()
        except OSError, ValueError:
            return ''

    def terminate(self, *, timeout: float = TERMINATE_TIMEOUT) -> None:
        """Stops the child: ``SIGTERM``, then ``SIGKILL`` if it ignores that."""

        if self.alive:
            self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=timeout)

    def close(self) -> None:
        """Releases the output file. The process, if alive, is left running."""

        self._output.close()
