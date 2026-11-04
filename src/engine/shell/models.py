"""Modelos de resultado de la ejecución de comandos."""

from __future__ import annotations

from pydantic import BaseModel

DEFAULT_TIMEOUT = 15
"""Timeout por comando, en segundos."""

MAX_TIMEOUT = 120
"""Tope del timeout que puede pedir el modelo."""

MAX_OUTPUT_BYTES = 64 * 1024
"""Tope de salida por stream (stdout y stderr se cuentan por separado)."""


class RunToolOutput(BaseModel):
    """Resultado de un comando ejecutado."""

    returncode: int
    stdout: str | None = None
    stderr: str | None = None
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False
