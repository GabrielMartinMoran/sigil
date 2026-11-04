"""Resultado de un comando ejecutado."""

from __future__ import annotations

from pydantic import BaseModel


class RunToolOutput(BaseModel):
    """Resultado de un comando ejecutado."""

    returncode: int
    stdout: str | None = None
    stderr: str | None = None
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
