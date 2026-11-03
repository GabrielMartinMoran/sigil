import asyncio
import os
import re
import selectors
import shlex
import signal
import subprocess
import time

import rich
from pydantic import BaseModel
from pydantic_ai import Agent

DEFAULT_TIMEOUT = 15
MAX_TIMEOUT = 120
MAX_OUTPUT_BYTES = 64 * 1024  # 64 KiB por stream

# Marca de entorno que heredan los procesos hijos. Si una tool intenta arrancar
# otra instancia de Sigil, `main.py` la rechaza en vez de recursar/bloquearse.
SELF_GUARD_ENV = 'SIGIL_SELF_GUARD'

# Programas que sólo envuelven a otro comando (p. ej. `uv run ./main.py`).
_WRAPPERS = {'env', 'exec', 'command', 'nohup', 'sudo', 'time', 'uv', 'run'}
_PYTHON_RE = re.compile(r'^python(\d+(\.\d+)?)?$')


class _RunToolOutput(BaseModel):
    returncode: int
    stdout: str | None = None
    stderr: str | None = None
    timed_out: bool = False
    stdout_truncated: bool = False
    stderr_truncated: bool = False


def _is_self_invocation(cmd: str) -> bool:
    """Detecta si el comando intenta arrancar Sigil (u `main.py`) a sí mismo."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        tokens = cmd.split()

    for segment in re.split(r'[;&|]+', ' '.join(tokens)):
        words = segment.split()
        index = 0
        while index < len(words):
            name = os.path.basename(words[index])
            if name in _WRAPPERS or _PYTHON_RE.match(name):
                index += 1
                continue
            break

        if index < len(words):
            program = os.path.basename(words[index])
            if program == 'sigil' or program in {'main.py', 'sigil.py'}:
                return True

    return False


def execute_command(cmd: str, timeout_seconds: int = DEFAULT_TIMEOUT) -> _RunToolOutput:
    """Ejecuta un comando de Bash no interactivo y devuelve su salida."""
    if not cmd.strip():
        return _RunToolOutput(returncode=2, stderr='Empty command.')

    if _is_self_invocation(cmd):
        return _RunToolOutput(
            returncode=126,
            stderr=(
                'Refusing to run nested Sigil: launching Sigil from inside the '
                f'agent is not allowed ({SELF_GUARD_ENV}=1).'
            ),
        )

    timeout = max(1, min(timeout_seconds, MAX_TIMEOUT))
    deadline = time.monotonic() + timeout

    try:
        process = subprocess.Popen(
            ['/bin/bash', '--noprofile', '--norc', '-c', cmd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env={
                **os.environ,
                'CI': '1',
                'GIT_TERMINAL_PROMPT': '0',
                SELF_GUARD_ENV: '1',
            },
        )
    except OSError as exc:
        return _RunToolOutput(returncode=127, stderr=str(exc))

    stdout = bytearray()
    stderr = bytearray()
    stdout_truncated = False
    stderr_truncated = False
    timed_out = False

    selector = selectors.DefaultSelector()

    try:
        assert process.stdout is not None
        assert process.stderr is not None

        for stream in (process.stdout, process.stderr):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, stream is process.stdout)

        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                timed_out = True
                break

            events = selector.select(timeout=min(remaining, 0.2))

            for key, _ in events:
                try:
                    chunk = os.read(key.fileobj.fileno(), 8192)
                except BlockingIOError:
                    continue

                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue

                is_stdout = key.data
                buffer = stdout if is_stdout else stderr

                available = max(0, MAX_OUTPUT_BYTES - len(buffer))
                buffer.extend(chunk[:available])

                if len(chunk) > available:
                    if is_stdout:
                        stdout_truncated = True
                    else:
                        stderr_truncated = True

        if timed_out:
            _kill_process_group(process)

        process.wait(timeout=2)

    except Exception as exc:
        _kill_process_group(process)
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass

        return _RunToolOutput(
            returncode=1,
            stdout=stdout.decode('utf-8', errors='replace'),
            stderr=str(exc),
        )

    finally:
        selector.close()

        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()

    return _RunToolOutput(
        returncode=124 if timed_out else process.returncode,
        stdout=stdout.decode('utf-8', errors='replace'),
        stderr=stderr.decode('utf-8', errors='replace'),
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
    )


def _kill_process_group(process: subprocess.Popen) -> None:
    """Termina todo el grupo de procesos del comando."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            process.kill()


async def execute_commands(
    cmds: list[str],
    timeout_seconds: int = DEFAULT_TIMEOUT,
) -> list[_RunToolOutput]:
    """Ejecuta varios comandos en Bash en paralelo y devuelve sus resultados.

    Cada comando se ejecuta de forma independiente en su propio proceso.
    `execute_command` es bloqueante (usa `selectors` para leer la salida), por
    lo que se despacha a un hilo para no bloquear el event loop y permitir que
    todos avancen a la vez.

    Se espera a que todos terminen (cada uno con su propio timeout) y se
    devuelven los resultados en el mismo orden que `cmds`. Un fallo o una
    excepción en un comando no impide que los demás se ejecuten: siempre se
    devuelve un resultado por comando.
    """
    if not cmds:
        return []

    results = await asyncio.gather(
        *(asyncio.to_thread(execute_command, cmd, timeout_seconds) for cmd in cmds),
        return_exceptions=True,
    )

    return [
        result
        if isinstance(result, _RunToolOutput)
        else _RunToolOutput(returncode=1, stderr=f'{type(result).__name__}: {result}')
        for result in results
    ]


class ToolManager:
    def __init__(self) -> None:
        self.tools = {}

    def register_tools(self, agent: Agent) -> None:

        @agent.tool_plain
        async def run(
            cmds: list[str],
            timeout_seconds: int = DEFAULT_TIMEOUT,
        ) -> list[_RunToolOutput]:
            """Execute several non-interactive Bash commands in parallel.

            Each command runs independently in its own process; arguments,
            pipes, redirects, and shell scripts are supported. Standard input is
            closed to prevent interactive blocking. Each command is terminated
            when its own timeout expires. Results are returned in the same order
            as `cmds`; a failed or timed-out command is reported in its result
            without affecting the others.

            Args:
                cmds: List of Bash commands to execute in parallel.
                timeout_seconds: Execution timeout in seconds (1-120), applied
                    to each command individually.
            """
            if not cmds:
                rich.print('[yellow]No commands to run.[/yellow]')
                return []

            for cmd in cmds:
                rich.print(
                    f'[bold green]Running command:[/bold green] [dodger_blue1]{cmd}[/dodger_blue1] '
                    f'[bold green](timeout: {timeout_seconds}s, parallel)[/bold green]'
                )
            return await execute_commands(cmds, timeout_seconds)
