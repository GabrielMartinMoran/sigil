"""Real supervisor failure and host-liveness contracts."""

import asyncio
import contextlib
import os
import signal

import pytest

from src.core.codemode.sandbox import SandboxUnavailable, execute_python


def test_supervisor_crash_cannot_return_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        ready = asyncio.Event()
        spawned = None
        spawn = asyncio.create_subprocess_exec

        async def capture(*args, **kwargs):
            nonlocal spawned
            process = await spawn(*args, **kwargs)
            if any(isinstance(arg, str) and arg.startswith('--unit=') for arg in args):
                spawned = process
            return process

        class Dispatcher:
            async def call(self, name, args):
                ready.set()
                await asyncio.Event().wait()

        monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)
        task = asyncio.create_task(execute_python('await tools.block()', dispatcher=Dispatcher()))
        try:
            await asyncio.wait_for(ready.wait(), timeout=5)
            assert spawned is not None
            os.kill(spawned.pid, signal.SIGKILL)
            with pytest.raises(SandboxUnavailable):
                await asyncio.wait_for(task, timeout=15)
        finally:
            if not task.done():
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError, SandboxUnavailable):
                await task

    asyncio.run(run())


def test_combined_output_limit_is_shared_between_streams() -> None:
    result = asyncio.run(
        execute_python(
            "import os\nos.write(1, b'a' * 96)\nos.write(2, b'b' * 96)",
            max_output_bytes=128,
        )
    )
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 128
    assert result.stdout_truncated or result.stderr_truncated


def test_host_crash_triggers_supervisor_cleanup() -> None:
    import json
    import sys

    worker = r"""
import asyncio, json
from src.core.codemode import sandbox
async def main():
    spawn = asyncio.create_subprocess_exec
    async def capture(*args, **kwargs):
        process = await spawn(*args, **kwargs)
        units = [arg[7:] for arg in args if isinstance(arg, str) and arg.startswith('--unit=')]
        if units:
            print(json.dumps({'pid': process.pid, 'unit': units[0]}), flush=True)
        return process
    asyncio.create_subprocess_exec = capture
    class Dispatcher:
        async def call(self, name, args):
            print('ready', flush=True)
            await asyncio.Event().wait()
    await sandbox.execute_python('await tools.block()', dispatcher=Dispatcher())
asyncio.run(main())
"""

    async def run() -> None:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            '-c',
            worker,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        unit = None
        waiter = None
        try:
            assert process.stdout is not None
            async with asyncio.timeout(10):
                info = json.loads(await process.stdout.readline())
                unit = info['unit']
                waiter = await asyncio.create_subprocess_exec(
                    '/usr/bin/python3',
                    '-I',
                    '-c',
                    'import os,select,sys; fd=os.pidfd_open(int(sys.argv[1])); '
                    'poll=select.poll(); poll.register(fd,select.POLLIN); '
                    "print('pinned',flush=True); sys.exit(0 if poll.poll(10000) else 1)",
                    str(info['pid']),
                    stdout=asyncio.subprocess.PIPE,
                )
                assert await waiter.stdout.readline() == b'pinned\n'
                assert await process.stdout.readline() == b'ready\n'
            process.kill()
            await process.wait()
            await asyncio.wait_for(waiter.wait(), timeout=12)
            assert waiter.returncode == 0
            query = await asyncio.create_subprocess_exec(
                'systemctl',
                '--user',
                'show',
                '--property=TasksCurrent',
                '--value',
                unit,
                stdout=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(query.communicate(), timeout=5)
            assert stdout.strip() in (b'0', b'[not set]')
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if waiter is not None and waiter.returncode is None:
                waiter.kill()
                await waiter.wait()
            if unit is not None:
                from src.core.codemode.sandbox import _signal_scope

                await _signal_scope(unit)

    asyncio.run(run())


def test_both_streams_can_overflow_without_breaking_cleanup() -> None:
    code = (
        'import os, threading\n'
        "threading.Thread(target=lambda: os.write(2, b'e' * 100000)).start()\n"
        "os.write(1, b'o' * 100000)"
    )
    result = asyncio.run(execute_python(code, max_output_bytes=128))
    assert len(result.stdout.encode()) + len(result.stderr.encode()) <= 128
    assert result.stdout_truncated or result.stderr_truncated


def test_supervisor_removes_payload_directory_after_setup_open_failure(tmp_path, monkeypatch) -> None:
    import socket
    import sys
    from pathlib import Path

    from src.core.codemode import supervisor

    membership = Path('/proc/self/cgroup').read_text().strip()[4:]
    root = tmp_path / membership
    root.mkdir(parents=True)
    (root / 'memory.max').write_text('536870912')
    (root / 'pids.max').write_text('32')
    (root / 'cpu.max').write_text('100000 100000')
    host, child = socket.socketpair()
    fd = child.detach()
    monkeypatch.setattr(supervisor, 'Path', lambda value: tmp_path if value == '/sys/fs/cgroup' else Path(value))
    monkeypatch.setattr(sys, 'argv', ['supervisor.py', str(fd), '-1', '/bin/true'])
    try:
        with pytest.raises(FileNotFoundError):
            supervisor.main()
        assert not (root / 'payload').exists()
    finally:
        host.close()
        with contextlib.suppress(OSError):
            os.close(fd)
