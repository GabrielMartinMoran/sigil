"""Result of running a script bundled in a skill."""

from __future__ import annotations

from pydantic import BaseModel


class ScriptRunResult(BaseModel):
    """Result of running a bundled script."""

    skill: str
    script: str
    returncode: int
    stdout: str | None = None
    stderr: str | None = None
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
