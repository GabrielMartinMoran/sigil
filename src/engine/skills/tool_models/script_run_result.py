"""Resultado de ejecutar un script empaquetado en un skill."""

from __future__ import annotations

from pydantic import BaseModel


class ScriptRunResult(BaseModel):
    """Resultado de ejecutar un script empaquetado."""

    skill: str
    script: str
    returncode: int
    stdout: str | None = None
    stderr: str | None = None
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
