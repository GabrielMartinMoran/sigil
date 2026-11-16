"""Integration proof for delegated payload cgroup lifecycle."""

import asyncio
import secrets
import shutil

import pytest


@pytest.mark.skipif(not shutil.which('systemd-run'), reason='systemd-run unavailable')
def test_delegated_scope_keeps_empty_payload_observable() -> None:
    helper = r"""
import os, pathlib, select, signal, sys
root = pathlib.Path('/sys/fs/cgroup') / pathlib.Path('/proc/self/cgroup').read_text().strip()[4:]
payload = root / 'sigil-payload-test'
payload.mkdir()
pid = os.fork()
if pid == 0:
    os.kill(os.getpid(), signal.SIGSTOP)
    os._exit(0)
try:
    _, status = os.waitpid(pid, os.WUNTRACED)
    if not os.WIFSTOPPED(status):
        raise RuntimeError('payload failed to stop')
    (payload / 'cgroup.procs').write_text(str(pid))
    with (payload / 'cgroup.events').open() as events:
        if 'populated 1' not in events.read():
            raise RuntimeError('payload not accounted')
        os.kill(pid, signal.SIGCONT)
        os.waitpid(pid, 0)
        pid = None
        poll = select.poll()
        poll.register(events, select.POLLPRI | select.POLLERR)
        while True:
            events.seek(0)
            if 'populated 0' in events.read():
                break
            if not poll.poll(5000):
                raise RuntimeError('payload did not drain')
        print('payload empty')
finally:
    if pid is not None:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    payload.rmdir()
"""

    async def run() -> None:
        unit = f'sigil-test-{secrets.token_hex(16)}.scope'
        process = await asyncio.create_subprocess_exec(
            'systemd-run',
            '--user',
            '--scope',
            f'--unit={unit}',
            '--quiet',
            '--property=Delegate=yes',
            '--property=MemoryMax=512M',
            '--property=TasksMax=32',
            '/usr/bin/python3',
            '-I',
            '-c',
            helper,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(10):
                stdout, stderr = await process.communicate()
        except BaseException:
            cleanup = await asyncio.create_subprocess_exec(
                'systemctl',
                '--user',
                'kill',
                '--kill-whom=all',
                '--signal=SIGKILL',
                unit,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                async with asyncio.timeout(5):
                    await cleanup.wait()
            finally:
                if cleanup.returncode is None:
                    cleanup.kill()
                    await cleanup.wait()
                if process.returncode is None:
                    process.kill()
                await process.wait()
            raise
        assert process.returncode == 0, stderr.decode()
        assert stdout.strip() == b'payload empty'

    asyncio.run(run())


def test_delegated_payload_can_be_killed_without_killing_supervisor() -> None:
    helper = r"""
import os, pathlib, select, signal, sys
root = pathlib.Path('/sys/fs/cgroup') / pathlib.Path('/proc/self/cgroup').read_text().strip()[4:]
payload = root / 'sigil-payload-kill-test'
payload.mkdir()
pid = os.fork()
if pid == 0:
    os.kill(os.getpid(), signal.SIGSTOP)
    os.execv('/usr/bin/python3', ['/usr/bin/python3', '-I', '-c', 'import signal; signal.pause()'])
try:
    _, status = os.waitpid(pid, os.WUNTRACED)
    if not os.WIFSTOPPED(status):
        raise RuntimeError('payload failed to stop')
    (payload / 'cgroup.procs').write_text(str(pid))
    with (payload / 'cgroup.events').open() as events:
        if 'populated 1' not in events.read():
            raise RuntimeError('payload not accounted')
        os.kill(pid, signal.SIGCONT)
        (payload / 'cgroup.kill').write_text('1')
        os.waitpid(pid, 0)
        pid = None
        poll = select.poll()
        poll.register(events, select.POLLPRI | select.POLLERR)
        while True:
            events.seek(0)
            if 'populated 0' in events.read():
                break
            if not poll.poll(5000):
                raise RuntimeError('payload did not drain')
        print('payload killed; supervisor alive')
finally:
    if pid is not None:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
    payload.rmdir()
"""

    async def run() -> None:
        unit = f'sigil-test-{secrets.token_hex(16)}.scope'
        process = await asyncio.create_subprocess_exec(
            'systemd-run',
            '--user',
            '--scope',
            f'--unit={unit}',
            '--quiet',
            '--property=Delegate=yes',
            '--property=MemoryMax=512M',
            '--property=TasksMax=32',
            '/usr/bin/python3',
            '-I',
            '-c',
            helper,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(10):
                stdout, stderr = await process.communicate()
        except BaseException:
            cleanup = await asyncio.create_subprocess_exec(
                'systemctl',
                '--user',
                'kill',
                '--kill-whom=all',
                '--signal=SIGKILL',
                unit,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                async with asyncio.timeout(5):
                    await cleanup.wait()
            finally:
                if cleanup.returncode is None:
                    cleanup.kill()
                    await cleanup.wait()
                if process.returncode is None:
                    process.kill()
                await process.wait()
            raise
        assert process.returncode == 0, stderr.decode()
        assert stdout.strip() == b'payload killed; supervisor alive'

    asyncio.run(run())
