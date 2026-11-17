"""Experimental Linux runtime with delegated payload lifecycle supervision."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import select
import shutil
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from src.core.codemode.broker import Dispatcher, serve_requests

MAX_CODE_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 64 * 1024
MAX_TIMEOUT_SECONDS = 120
DEFAULT_TIMEOUT_SECONDS = 120
MAX_TEMP_BYTES = 8 * 1024 * 1024
MAX_SCOPE_MEMORY = 512 * 1024 * 1024
MAX_SCOPE_TASKS = 32
MAX_CONCURRENT_SANDBOXES = 2
_ADMISSION = threading.BoundedSemaphore(MAX_CONCURRENT_SANDBOXES)
_CGROUP_ROOT = Path('/sys/fs/cgroup')
_READY = b'\x1eSIGIL_SANDBOX_READY\n'

_BOOTSTRAP = """\
import ast
import asyncio
import inspect
import json
import resource
import socket
import sys

resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
sys.stdout.buffer.write(b'\\x1eSIGIL_SANDBOX_READY\\n')
sys.stdout.buffer.flush()
source = sys.stdin.read()
sys.stdin.close()
code = compile(source, '<execute_code>', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

class ToolClient:
    def __init__(self, reader, writer):
        self.reader = reader
        self.writer = writer
        self.next_id = 0
        self.pending = {}
        self.lock = asyncio.Lock()
        self.reader_task = asyncio.create_task(self._read())

    async def _read(self):
        try:
            while line := await self.reader.readline():
                message = json.loads(line)
                future = self.pending.pop(message.get('id'), None)
                if future is None or future.done():
                    continue
                if 'error' in message:
                    future.set_exception(RuntimeError(message['error']['message']))
                else:
                    future.set_result(message['result'])
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('Tool channel closed'))
            self.pending.clear()

    async def call(self, name, **kwargs):
        async with self.lock:
            self.next_id += 1
            request_id = self.next_id
            payload = json.dumps({'id': request_id, 'name': name, 'args': kwargs}).encode() + b'\\n'
            if len(payload) > 65536:
                raise ValueError('Tool request too large')
            future = asyncio.get_running_loop().create_future()
            self.pending[request_id] = future
            self.writer.write(payload)
            await self.writer.drain()
        return await future

    def __getattr__(self, name):
        async def invoke(**kwargs):
            return await self.call(name, **kwargs)
        return invoke

async def main():
    namespace = {'__name__': '__main__'}
    client = None
    if len(sys.argv) > 1:
        sock = socket.socket(fileno=int(sys.argv[1]))
        sock.setblocking(False)
        reader, writer = await asyncio.open_connection(sock=sock, limit=1048577)
        client = ToolClient(reader, writer)
        namespace['tools'] = client
    try:
        result = eval(code, namespace)
        if inspect.isawaitable(result):
            await result
    finally:
        if client is not None:
            client.writer.close()
            await client.writer.wait_closed()
            client.reader_task.cancel()
            await asyncio.gather(client.reader_task, return_exceptions=True)

asyncio.run(main())
"""


class SandboxUnavailable(RuntimeError):
    """The isolated runtime cannot be started."""


@dataclass(frozen=True, slots=True)
class SandboxResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    stdout_truncated: bool
    stderr_truncated: bool


def _kill(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


async def _signal_scope(unit: str) -> None:
    """Signal every member of a transient scope, or verify it is already absent."""
    systemctl = shutil.which('systemctl')
    if systemctl is None:
        raise SandboxUnavailable('Cannot clean sandbox scope: systemctl is unavailable')

    async def command(*args: str) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            systemctl, '--user', *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        try:
            async with asyncio.timeout(5):
                stdout, stderr = await process.communicate()
        except BaseException:
            process.kill()
            await process.wait()
            raise
        return process.returncode or 0, stdout, stderr

    status, _, stderr = await command('kill', '--kill-whom=all', '--signal=SIGKILL', unit)
    if status:
        state_status, state, _ = await command('show', '--property=LoadState', '--value', unit)
        if not state_status and state.strip() == b'not-found':
            return
        if not state_status and state.strip() == b'loaded':
            retry_status, _, retry_error = await command('kill', '--kill-whom=all', '--signal=SIGKILL', unit)
            if retry_status == 0:
                return
            stderr = retry_error
        raise SandboxUnavailable(f'Cannot clean sandbox scope: {stderr.decode("utf-8", "replace").strip()}')


def _open_cgroup_events(group: str, unit: str) -> int:
    """Pin the verified cgroup before accepting untrusted source."""
    root = _CGROUP_ROOT
    relative = Path(group.removeprefix('/'))
    if not group.startswith('/') or '..' in relative.parts or relative.name != unit:
        raise SandboxUnavailable('Cannot verify sandbox cgroup path')
    parent = root / relative.parent
    if not (root / 'cgroup.controllers').is_file() or not parent.is_dir():
        raise SandboxUnavailable('Cannot locate sandbox cgroup hierarchy')
    directory = parent / relative.name
    events = directory / 'cgroup.events'
    try:
        return os.open(events, os.O_RDONLY | os.O_CLOEXEC)
    except OSError as exc:
        raise SandboxUnavailable('Sandbox cgroup event file is unavailable') from exc


def _wait_for_empty_fd(fd: int) -> None:
    """Wait for the kernel's recursive populated notification."""
    try:
        watcher = select.poll()
        watcher.register(fd, select.POLLPRI | select.POLLERR)
        deadline = time.monotonic() + 5
        while True:
            content = os.pread(fd, 4096, 0).decode('ascii')
            values = dict(line.split() for line in content.splitlines())
            if values.get('populated') == '0':
                return
            if values.get('populated') != '1':
                raise SandboxUnavailable('Cannot parse sandbox cgroup state')
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not watcher.poll(max(1, int(remaining * 1000))):
                raise SandboxUnavailable('Sandbox cgroup did not become empty')
    except OSError as exc:
        raise SandboxUnavailable(f'Cannot monitor sandbox cgroup: {exc}') from exc


def _wait_for_empty_cgroup(group: str, unit: str) -> None:
    fd = _open_cgroup_events(group, unit)
    try:
        _wait_for_empty_fd(fd)
    finally:
        os.close(fd)


async def _verify_scope_empty(unit: str) -> None:
    systemctl = shutil.which('systemctl')
    if systemctl is None:
        raise SandboxUnavailable('Cannot verify sandbox cleanup: systemctl is unavailable')
    waiter = await asyncio.create_subprocess_exec(
        systemctl, '--user', 'wait', unit, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        async with asyncio.timeout(5):
            await waiter.wait()
    except BaseException:
        waiter.kill()
        await waiter.wait()
        raise
    process = await asyncio.create_subprocess_exec(
        systemctl,
        '--user',
        'show',
        '--property=LoadState',
        '--property=TasksCurrent',
        '--property=ControlGroup',
        '--property=ActiveState',
        unit,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(5):
            stdout, stderr = await process.communicate()
    except BaseException:
        process.kill()
        await process.wait()
        raise
    properties = dict(line.split('=', 1) for line in stdout.decode('utf-8', 'replace').splitlines() if '=' in line)
    if process.returncode == 0:
        if properties.get('LoadState') == 'not-found' or properties.get('TasksCurrent') == '0':
            return
        group = properties.get('ControlGroup', '')
        if not group and properties.get('ActiveState') in ('inactive', 'failed'):
            return
        if group:
            await asyncio.to_thread(_wait_for_empty_cgroup, group, unit)
            return
    raise SandboxUnavailable(
        f'Cannot confirm sandbox scope is empty: {properties}; {stderr.decode("utf-8", "replace").strip()}'
    )


async def _cleanup_scope(unit: str, process: asyncio.subprocess.Process, cgroup_fd: int | None = None) -> None:
    """Keep cleanup running if the caller is cancelled again."""
    cleanup = _cleanup_scope_impl(unit, process) if cgroup_fd is None else _cleanup_scope_impl(unit, process, cgroup_fd)
    task = asyncio.create_task(cleanup)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


async def _cleanup_scope_impl(unit: str, process: asyncio.subprocess.Process, cgroup_fd: int | None = None) -> None:
    try:
        await _signal_scope(unit)
    finally:
        _kill(process)
        if process.stdin is not None:
            process.stdin.close()
        try:
            async with asyncio.timeout(5):
                await process.wait()
        finally:
            await _signal_scope(unit)
            if cgroup_fd is None:
                await _verify_scope_empty(unit)
            else:
                await asyncio.to_thread(_wait_for_empty_fd, cgroup_fd)


async def execute_python(
    code: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    dispatcher: Dispatcher | None = None,
) -> SandboxResult:
    """Run a script with a configurable wall-clock timeout of up to 120 seconds.

    The timeout covers setup and execution, including nested tool calls. Cleanup
    happens after the deadline; already completed external effects cannot be undone.
    """
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError('Invalid sandbox timeout')
    if not _ADMISSION.acquire(blocking=False):
        raise SandboxUnavailable('Sandbox concurrency limit reached; retry when a script finishes')
    try:
        deadline = asyncio.timeout(timeout_seconds)
        try:
            async with deadline:
                return await _execute_python(
                    code, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes, dispatcher=dispatcher
                )
        except TimeoutError:
            if not deadline.expired():
                raise
            return SandboxResult(None, '', 'Execution timed out; partial output is unavailable.', True, False, False)
    finally:
        _ADMISSION.release()


async def _execute_python(
    code: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    dispatcher: Dispatcher | None = None,
) -> SandboxResult:
    """Execute only in a verified delegated cgroup and bubblewrap namespace."""
    if len(code.encode('utf-8')) > MAX_CODE_BYTES:
        raise ValueError('Code exceeds the size limit')
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS or not 0 < max_output_bytes <= MAX_OUTPUT_BYTES:
        raise ValueError('Invalid sandbox timeout or output limit')
    bwrap = shutil.which('bwrap')
    systemd_run = shutil.which('systemd-run')
    if sys.platform != 'linux' or bwrap is None:
        raise SandboxUnavailable('Linux bubblewrap is required')
    if systemd_run is None or shutil.which('systemctl') is None:
        raise SandboxUnavailable('systemd-run and systemctl are required for sandbox resource limits and cleanup')
    unit = f'sigil-code-{secrets.token_hex(16)}.scope'
    host_control, child_control = socket.socketpair()
    parent_socket, child_socket = socket.socketpair() if dispatcher is not None else (None, None)
    broker_fd = child_socket.fileno() if child_socket is not None else -1
    fds = (child_control.fileno(),) + ((broker_fd,) if broker_fd >= 0 else ())
    mounts: list[str] = []
    for path in ('/usr', '/lib', '/lib64'):
        if Path(path).exists():
            mounts.extend(('--ro-bind', path, path))
    process = None
    writer = None
    broker_task = None
    cgroup_fd = None
    captures: list[asyncio.Task[tuple[bytes, bool]]] = []
    finish_task = None
    try:
        spawn = asyncio.create_task(
            asyncio.create_subprocess_exec(
                systemd_run,
                '--user',
                '--scope',
                f'--unit={unit}',
                '--quiet',
                '--property=Delegate=yes',
                '--property=RuntimeMaxSec=140',
                '--property=KillMode=control-group',
                '--property=SendSIGKILL=yes',
                '--property=CPUQuota=100%',
                f'--property=MemoryMax={MAX_SCOPE_MEMORY}',
                f'--property=TasksMax={MAX_SCOPE_TASKS}',
                '/usr/bin/python3',
                '-I',
                str(Path(__file__).with_name('supervisor.py')),
                str(child_control.fileno()),
                str(broker_fd),
                bwrap,
                '--unshare-all',
                '--die-with-parent',
                '--new-session',
                '--clearenv',
                *mounts,
                '--proc',
                '/proc',
                '--dev',
                '/dev',
                '--size',
                str(MAX_TEMP_BYTES),
                '--tmpfs',
                '/tmp',
                '--chdir',
                '/tmp',
                '--',
                '/usr/bin/python3',
                '-I',
                '-c',
                _BOOTSTRAP,
                *([str(broker_fd)] if broker_fd >= 0 else []),
                pass_fds=fds,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        )
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            while not spawn.done():
                try:
                    await asyncio.shield(spawn)
                except asyncio.CancelledError:
                    continue
            process = spawn.result()
            raise
        child_control.close()
        if child_socket is not None:
            child_socket.close()
        host_control.setblocking(False)
        reader, writer = await asyncio.open_connection(sock=host_control, limit=4096)
        identity = json.loads(await reader.readline())
        group = identity.get('ready', '')
        if identity.get('pid') != process.pid or not isinstance(group, str):
            raise SandboxUnavailable('Sandbox launcher identity could not be verified')
        if not group.endswith(f'/{unit}/payload'):
            raise SandboxUnavailable('Sandbox payload identity could not be verified')
        cgroup_fd = _open_cgroup_events(group, 'payload')
        if b'populated 1' not in os.pread(cgroup_fd, 4096, 0):
            raise SandboxUnavailable('Sandbox payload is not accounted for')
        if parent_socket is not None:
            assert dispatcher is not None
            parent_socket.setblocking(False)
            broker_reader, broker_writer = await asyncio.open_connection(sock=parent_socket)

            async def broker() -> None:
                try:
                    await serve_requests(broker_reader, broker_writer, dispatcher)
                finally:
                    broker_writer.close()
                    await broker_writer.wait_closed()

            broker_task = asyncio.create_task(broker())
        writer.write(b'G')
        await writer.drain()
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        process.stdin.write(code.encode('utf-8'))
        await process.stdin.drain()
        process.stdin.close()
        # One shared retained-byte budget; the bootstrap marker is not user output.
        remaining = max_output_bytes + len(_READY)

        async def capture(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
            nonlocal remaining
            saved = bytearray()
            truncated = False
            while chunk := await stream.read(8192):
                keep = min(len(chunk), remaining)
                remaining -= keep
                saved.extend(chunk[:keep])
                if keep < len(chunk) and not truncated:
                    truncated = True
                    writer.write(b'K')
                    await writer.drain()
            return bytes(saved), truncated

        captures = [asyncio.create_task(capture(process.stdout)), asyncio.create_task(capture(process.stderr))]

        async def finish() -> int:
            terminal = json.loads(await reader.readline())
            if type(terminal.get('done')) is not int:
                raise SandboxUnavailable('Supervisor closed without a terminal status')
            assert cgroup_fd is not None
            await asyncio.to_thread(_wait_for_empty_fd, cgroup_fd)
            async with asyncio.timeout(5):
                await asyncio.gather(*captures)
                writer.write(b'A')
                await writer.drain()
                await process.wait()
            if process.returncode != 0:
                raise SandboxUnavailable('Supervisor failed during cleanup')
            return terminal['done']

        finish_task = asyncio.create_task(finish())
        returncode = await asyncio.shield(finish_task)
        stdout, stdout_truncated = captures[0].result()
        stderr, stderr_truncated = captures[1].result()
        if not stdout.startswith(_READY):
            raise SandboxUnavailable(f'Sandbox setup failed: {stderr.decode("utf-8", "replace")}')
        return SandboxResult(
            returncode,
            stdout[len(_READY) :].decode('utf-8', 'replace'),
            stderr.decode('utf-8', 'replace'),
            False,
            stdout_truncated,
            stderr_truncated,
        )
    except BaseException as error:
        if broker_task is not None:
            broker_task.cancel()

        async def cleanup() -> None:
            try:
                if writer is not None and finish_task is not None and not finish_task.done():
                    writer.write(b'K')
                    await writer.drain()
                    async with asyncio.timeout(8):
                        await asyncio.shield(finish_task)
                elif process is not None:
                    await _cleanup_scope(unit, process)
            except BaseException:
                if process is not None:
                    await _cleanup_scope(unit, process)
                raise

        task = asyncio.create_task(cleanup())
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        try:
            task.result()
        except (SandboxUnavailable, OSError, ValueError, TimeoutError, asyncio.CancelledError) as cleanup_error:
            raise SandboxUnavailable(f'Sandbox cleanup failed for {unit}: {cleanup_error}') from error
        if isinstance(error, (OSError, ValueError)):
            raise SandboxUnavailable(f'Cannot start sandbox: {error}') from error
        raise
    finally:
        if finish_task is not None and not finish_task.done():
            finish_task.cancel()
            await asyncio.gather(finish_task, return_exceptions=True)
        for task in captures:
            if not task.done():
                task.cancel()
        await asyncio.gather(*captures, return_exceptions=True)
        if broker_task is not None:
            broker_task.cancel()
            await asyncio.gather(broker_task, return_exceptions=True)
        if cgroup_fd is not None:
            os.close(cgroup_fd)
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        host_control.close()
        child_control.close()
        if parent_socket is not None:
            parent_socket.close()
        if child_socket is not None:
            child_socket.close()
