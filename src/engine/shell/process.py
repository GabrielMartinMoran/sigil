"""Spawning and draining of a non-interactive shell process.

Separated from the public API because they are two distinct responsibilities: here lives
the ``subprocess`` and ``selectors`` handling; in ``runner`` lives the policy
(timeouts, guards, results).
"""

from __future__ import annotations

import os
import selectors
import signal
import subprocess
import time
from dataclasses import dataclass, field

from src.engine.shell.environment import child_environment
from src.engine.shell.limits import MAX_OUTPUT_BYTES

_READ_CHUNK_BYTES = 8192
_SELECT_SLICE_SECONDS = 0.2
_KILL_GRACE_SECONDS = 2


@dataclass(slots=True)
class DrainResult:
    """Captured output of a process."""

    stdout: bytes = b''
    stderr: bytes = b''
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    returncode: int | None = None


@dataclass(slots=True)
class _Buffers:
    """Output accumulators with their truncation flag."""

    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    def extend(self, chunk: bytes, *, is_stdout: bool) -> None:
        buffer = self.stdout if is_stdout else self.stderr
        available = max(0, MAX_OUTPUT_BYTES - len(buffer))
        buffer.extend(chunk[:available])
        if len(chunk) > available:
            if is_stdout:
                self.stdout_truncated = True
            else:
                self.stderr_truncated = True


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


def kill_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminates the whole process group of the command."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            process.kill()


def _read_available(selector: selectors.BaseSelector, buffers: _Buffers) -> None:
    """Reads whatever is ready and unregisters the streams that closed."""
    for key, _ in selector.select(timeout=_SELECT_SLICE_SECONDS):
        try:
            chunk = os.read(key.fileobj.fileno(), _READ_CHUNK_BYTES)
        except BlockingIOError:
            continue

        if not chunk:
            selector.unregister(key.fileobj)
            key.fileobj.close()
            continue

        buffers.extend(chunk, is_stdout=bool(key.data))


def drain(process: subprocess.Popen[bytes], deadline: float) -> DrainResult:
    """Reads stdout and stderr until the process ends or the deadline expires."""
    buffers = _Buffers()
    timed_out = False
    selector = selectors.DefaultSelector()

    try:
        assert process.stdout is not None
        assert process.stderr is not None

        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, stream is process.stdout)

        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            _read_available(selector, buffers)

        if not timed_out:
            # Closed pipes do not imply exit. Waiting here avoids selecting an
            # empty descriptor set while preserving the command's own deadline.
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                timed_out = True
        if timed_out:
            kill_process_group(process)
            process.wait(timeout=_KILL_GRACE_SECONDS)
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()

    return DrainResult(
        stdout=bytes(buffers.stdout),
        stderr=bytes(buffers.stderr),
        stdout_truncated=buffers.stdout_truncated,
        stderr_truncated=buffers.stderr_truncated,
        timed_out=timed_out,
        returncode=process.returncode,
    )
