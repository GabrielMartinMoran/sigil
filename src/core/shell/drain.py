"""The drain helper of the core layer."""

from __future__ import annotations

import os
import selectors
import subprocess
import time
from dataclasses import dataclass, field

from src.core.shell.drain_result import DrainResult
from src.core.shell.kill_process_group import kill_process_group
from src.core.shell.limits import MAX_OUTPUT_BYTES

_READ_CHUNK_BYTES = 8192


_SELECT_SLICE_SECONDS = 0.2


_KILL_GRACE_SECONDS = 2


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
