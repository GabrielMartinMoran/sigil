"""The DrainResult value of the core layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DrainResult:
    """Captured output of a process."""

    stdout: bytes = b''
    stderr: bytes = b''
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    timed_out: bool = False
    returncode: int | None = None
