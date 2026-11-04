"""Ejecución de comandos de shell: política de timeouts, guardas y resultados.

La API pública es ``execute_command`` (uno) y ``execute_commands`` (varios en
paralelo). Ninguna lanza: un comando que falla, expira o no existe vuelve como
resultado con su código de salida, porque el modelo necesita leer el fallo.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time

from src.engine.shell.guard import SELF_GUARD_ENV, is_self_invocation
from src.engine.shell.models import DEFAULT_TIMEOUT, MAX_TIMEOUT, RunToolOutput
from src.engine.shell.process import DrainResult, drain, kill_process_group, spawn

TIMEOUT_RETURNCODE = 124
"""Código de salida convencional de ``timeout(1)``, para un comando que expiró."""

_REFUSED_RETURNCODE = 126
_NOT_FOUND_RETURNCODE = 127
_USAGE_RETURNCODE = 2


def _clamp_timeout(timeout_seconds: int) -> int:
    return max(1, min(timeout_seconds, MAX_TIMEOUT))


def _resolve_cwd(cwd: str | os.PathLike[str] | None) -> tuple[str | None, RunToolOutput | None]:
    """Valida el ``cwd`` pedido. Devuelve ``(cwd, error)``."""
    if cwd is None:
        return None, None
    cwd_path = os.fspath(cwd)
    if not os.path.isdir(cwd_path):
        return None, RunToolOutput(returncode=_USAGE_RETURNCODE, stderr=f'Working directory does not exist: {cwd_path}')
    return cwd_path, None


def _refusal() -> RunToolOutput:
    return RunToolOutput(
        returncode=_REFUSED_RETURNCODE,
        stderr=(
            f'Refusing to run nested Sigil: launching Sigil from inside the agent is not allowed ({SELF_GUARD_ENV}=1).'
        ),
    )


def _from_drain(result: DrainResult) -> RunToolOutput:
    """Traduce el resultado del drenaje a la salida pública."""
    return RunToolOutput(
        returncode=TIMEOUT_RETURNCODE if result.timed_out else (result.returncode or 0),
        stdout=result.stdout.decode('utf-8', errors='replace'),
        stderr=result.stderr.decode('utf-8', errors='replace'),
        timed_out=result.timed_out,
        stdout_truncated=result.stdout_truncated,
        stderr_truncated=result.stderr_truncated,
    )


def execute_command(
    cmd: str,
    timeout_seconds: int = DEFAULT_TIMEOUT,
    *,
    cwd: str | os.PathLike[str] | None = None,
    extra_env: dict[str, str] | None = None,
    check_self_invocation: bool = True,
) -> RunToolOutput:
    """Ejecuta un comando de Bash no interactivo y devuelve su salida.

    Args:
        cmd: Comando a ejecutar.
        timeout_seconds: Timeout en segundos (se recorta a 1-120).
        cwd: Directorio de trabajo del proceso hijo.
        extra_env: Variables de entorno adicionales para el proceso hijo.
        check_self_invocation: Si es ``False``, no se rechaza el comando por
            parecer una invocación anidada de Sigil. Los scripts de skills lo
            usan para permitir nombres como ``main.py``; el guard de entorno
            (``SELF_GUARD_ENV``) sigue activo en el proceso hijo.
    """
    if not cmd.strip():
        return RunToolOutput(returncode=_USAGE_RETURNCODE, stderr='Empty command.')

    if check_self_invocation and is_self_invocation(cmd):
        return _refusal()

    cwd_path, cwd_error = _resolve_cwd(cwd)
    if cwd_error is not None:
        return cwd_error

    deadline = time.monotonic() + _clamp_timeout(timeout_seconds)

    try:
        process = spawn(cmd, cwd=cwd_path, extra_env=extra_env)
    except OSError as exc:
        return RunToolOutput(returncode=_NOT_FOUND_RETURNCODE, stderr=str(exc))

    try:
        return _from_drain(drain(process, deadline))
    except Exception as exc:  # noqa: BLE001 - un fallo de drenaje no debe tumbar el turno
        kill_process_group(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        return RunToolOutput(returncode=1, stderr=str(exc))


async def execute_commands(
    cmds: list[str],
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> list[RunToolOutput]:
    """Ejecuta varios comandos en Bash en paralelo y devuelve sus resultados.

    Cada comando corre de forma independiente en su propio proceso.
    ``execute_command`` es bloqueante (usa ``selectors`` para leer la salida), así
    que se despacha a un hilo para no bloquear el event loop y permitir que todos
    avancen a la vez.

    Se espera a que todos terminen (cada uno con su propio timeout) y se devuelven
    los resultados en el mismo orden que ``cmds``. Un fallo o una excepción en un
    comando no impide que los demás se ejecuten: siempre hay un resultado por
    comando.
    """
    if not cmds:
        return []

    results = await asyncio.gather(
        *(asyncio.to_thread(execute_command, cmd, timeout_seconds) for cmd in cmds),
        return_exceptions=True,
    )

    return [
        result
        if isinstance(result, RunToolOutput)
        else RunToolOutput(returncode=1, stderr=f'{type(result).__name__}: {result}')
        for result in results
    ]
