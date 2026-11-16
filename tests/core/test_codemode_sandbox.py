"""The sandbox runs isolated code with bounded execution and output."""

import asyncio
import shutil
import sys
from pathlib import Path

import pytest

from src.core.codemode.sandbox import SandboxUnavailable, execute_python

pytestmark = pytest.mark.skipif(
    sys.platform != 'linux' or not shutil.which('bwrap'), reason='requires Linux bubblewrap'
)


def test_async_code_and_separate_output() -> None:
    async def run() -> None:
        result = await execute_python(
            "import asyncio, sys\nawait asyncio.sleep(0)\nprint('ok')\nprint('err', file=sys.stderr)"
        )
        assert result.returncode == 0
        assert result.stdout == 'ok\n'
        assert result.stderr == 'err\n'

    asyncio.run(run())


def test_sandbox_cannot_read_host_paths_or_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = tmp_path / 'private-marker'
    marker.write_text('secret sentinel')
    monkeypatch.setenv('SIGIL_SANDBOX_TEST_SECRET', 'secret sentinel')

    async def run() -> None:
        code = f"import os\nprint(os.path.exists({str(marker)!r}))\nprint(os.getenv('SIGIL_SANDBOX_TEST_SECRET'))"
        result = await execute_python(code)
        assert result.returncode == 0
        assert result.stdout == 'False\nNone\n'

    asyncio.run(run())


def test_timeout_terminates_code() -> None:
    async def run() -> None:
        result = await execute_python('while True: pass', timeout_seconds=1)
        assert result.timed_out

    asyncio.run(run())


def test_output_is_bounded() -> None:
    async def run() -> None:
        result = await execute_python("print('a' * 100000)", max_output_bytes=128)
        assert len(result.stdout.encode()) == 128
        assert result.stdout_truncated

    asyncio.run(run())


def test_missing_sandbox_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    with pytest.raises(SandboxUnavailable):
        asyncio.run(execute_python("print('must not run')"))


def test_descendant_holding_output_cannot_hang_runner() -> None:
    async def run() -> None:
        code = "import subprocess\nsubprocess.Popen(['/usr/bin/python3', '-c', 'while True: pass'])"
        result = await asyncio.wait_for(execute_python(code, timeout_seconds=1), timeout=5)
        assert result.timed_out or result.returncode == 0

    asyncio.run(run())


def test_python_failure_is_reported() -> None:
    async def run() -> None:
        result = await execute_python("raise ValueError('bad input')")
        assert result.returncode != 0
        assert 'ValueError: bad input' in result.stderr

    asyncio.run(run())


def test_failed_sandbox_setup_is_not_reported_as_user_code_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    executable = shutil.which('false')
    assert executable is not None
    monkeypatch.setattr(shutil, 'which', lambda name: executable)
    with pytest.raises(SandboxUnavailable):
        asyncio.run(execute_python("print('must not run')"))


def test_cancellation_terminates_sandbox_process(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        real_spawn = asyncio.create_subprocess_exec
        spawned: asyncio.Future[asyncio.subprocess.Process] = asyncio.get_running_loop().create_future()

        async def capture_spawn(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            if not spawned.done():
                spawned.set_result(process)
            return process

        monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture_spawn)
        task = asyncio.create_task(execute_python('while True: pass'))
        process = await asyncio.wait_for(spawned, timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(process.wait(), timeout=5)
        assert process.returncode is not None

    asyncio.run(run())


def test_sandbox_calls_host_tools_asynchronously() -> None:
    async def run() -> None:
        class FakeDispatcher:
            async def call(self, name, args):
                if name != 'read_file':
                    raise PermissionError('tool denied')
                return {'path': args['path']}

        code = """\
import asyncio
left, right = await asyncio.gather(
    tools.read_file(path='a.txt'),
    tools.read_file(path='b.txt'),
)
print(left['path'], right['path'])
"""
        result = await execute_python(code, dispatcher=FakeDispatcher())
        assert result.returncode == 0, result.stderr
        assert result.stdout == 'a.txt b.txt\n'

    asyncio.run(run())


def test_cancellation_during_spawn_reaps_process(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        real_spawn = asyncio.create_subprocess_exec
        spawned: asyncio.Future[asyncio.subprocess.Process] = asyncio.get_running_loop().create_future()
        resume = asyncio.Event()

        async def delayed_spawn(*args, **kwargs):
            process = await real_spawn(*args, **kwargs)
            if not spawned.done():
                spawned.set_result(process)
                await resume.wait()
            return process

        monkeypatch.setattr(asyncio, 'create_subprocess_exec', delayed_spawn)
        task = asyncio.create_task(execute_python('while True: pass'))
        process = await asyncio.wait_for(spawned, timeout=5)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        resume.set()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(process.wait(), timeout=1)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(run())


def test_sandbox_temporary_storage_is_bounded() -> None:
    async def run() -> None:
        code = """\
import errno
try:
    with open('/tmp/large', 'wb') as handle:
        for _ in range(16):
            handle.write(b'x' * (1024 * 1024))
    print('unbounded')
except OSError as exc:
    print('bounded' if exc.errno == errno.ENOSPC else 'unexpected')
"""
        result = await execute_python(code)
        assert result.returncode == 0, result.stderr
        assert result.stdout == 'bounded\n'

    asyncio.run(run())


def test_timeout_reaps_descendant_in_a_new_session() -> None:
    async def run() -> None:
        code = """\
import subprocess
subprocess.Popen(['/usr/bin/python3', '-c', 'while True: pass'], start_new_session=True)
while True:
    pass
"""
        result = await asyncio.wait_for(execute_python(code, timeout_seconds=1), timeout=5)
        assert result.timed_out

    asyncio.run(run())


def test_output_flood_stops_execution_before_timeout() -> None:
    async def run() -> None:
        code = "import os\nwhile True: os.write(1, b'x' * 8192)"
        result = await execute_python(code, timeout_seconds=5, max_output_bytes=128)
        assert result.stdout_truncated
        assert not result.timed_out
        assert len(result.stdout.encode()) == 128

    asyncio.run(run())


def test_sandbox_has_private_network_namespace() -> None:
    async def run() -> None:
        result = await execute_python('import socket\nprint([name for _, name in socket.if_nameindex()])')
        assert result.returncode == 0, result.stderr
        assert result.stdout == "['lo']\n"

    asyncio.run(run())


def test_cancel_stops_inflight_nested_call() -> None:
    async def run() -> None:
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class Dispatcher:
            async def call(self, name, args):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        task = asyncio.create_task(execute_python("await tools.read_file(path='a')", dispatcher=Dispatcher()))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), 5)

    asyncio.run(run())
