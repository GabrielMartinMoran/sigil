"""Shell command execution: timeout policy, guards and results.

The public API is ``execute_command`` (one) and ``execute_commands`` (several in
parallel). Neither raises: a command that fails, expires or does not exist comes back as
a result with its exit code, because the model needs to read the failure.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time

from src.engine.shell import running
from src.engine.shell.guard import SELF_GUARD_ENV, is_self_invocation
from src.engine.shell.limits import DEFAULT_TIMEOUT, MAX_TIMEOUT
from src.engine.shell.models.run_tool_output import RunToolOutput
from src.engine.shell.process import DrainResult, drain, kill_process_group, spawn

TIMEOUT_RETURNCODE = 124
"""Conventional exit code of ``timeout(1)``, for a command that expired."""

_REFUSED_RETURNCODE = 126
_NOT_FOUND_RETURNCODE = 127
_USAGE_RETURNCODE = 2


def _clamp_timeout(timeout_seconds: int) -> int:
    return max(1, min(timeout_seconds, MAX_TIMEOUT))


def _resolve_cwd(cwd: str | os.PathLike[str] | None) -> tuple[str | None, RunToolOutput | None]:
    """Validates the requested ``cwd``. Returns ``(cwd, error)``."""
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
    """Translates the drain result into the public output."""
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
    """Runs a non-interactive Bash command and returns its output.

    Args:
        cmd: Command to run.
        timeout_seconds: Timeout in seconds (clamped to 1-120).
        cwd: Working directory of the child process.
        extra_env: Extra environment variables for the child process.
        check_self_invocation: If ``False``, the command is not rejected for
            looking like a nested Sigil invocation. Skill scripts use it
            to allow names like ``main.py``; the environment guard
            (``SELF_GUARD_ENV``) stays active in the child process.
    """
    if not cmd.strip():
        return RunToolOutput(returncode=_USAGE_RETURNCODE, stderr='Empty command.')

    if check_self_invocation and is_self_invocation(cmd):
        return _refusal()

    cwd_path, cwd_error = _resolve_cwd(cwd)
    if cwd_error is not None:
        return cwd_error

    try:
        process = spawn(cmd, cwd=cwd_path, extra_env=extra_env)
    except OSError as exc:
        return RunToolOutput(returncode=_NOT_FOUND_RETURNCODE, stderr=str(exc))

    deadline = time.monotonic() + _clamp_timeout(timeout_seconds)

    # Registered while it runs, so an aborted turn can kill it instead of
    # leaving it working until its own timeout.
    running.track(process)
    try:
        return _from_drain(drain(process, deadline))
    except Exception as exc:  # noqa: BLE001 - a drain failure must not bring the turn down
        kill_process_group(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        return RunToolOutput(returncode=1, stderr=str(exc))
    finally:
        running.forget(process)


async def execute_commands(
    cmds: list[str],
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> list[RunToolOutput]:
    """Runs several Bash commands in parallel and returns their results.

    Each command runs independently in its own process.
    ``execute_command`` is blocking (it uses ``selectors`` to read the output), so
    it is dispatched to a thread so as not to block the event loop and allow every
    one to advance at the same time.

    It waits for all of them to finish (each with its own timeout) and returns
    the results in the same order as ``cmds``. A failure or an exception in one
    command does not prevent the others from running: there is always one result per
    command.
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
