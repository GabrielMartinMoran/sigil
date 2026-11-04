"""Result of an executed command."""

from __future__ import annotations

from pydantic import BaseModel


class RunToolOutput(BaseModel):
    """Result of an executed command."""

    returncode: int
    stdout: str | None = None
    stderr: str | None = None
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
