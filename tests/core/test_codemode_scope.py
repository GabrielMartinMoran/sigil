"""The code sandbox must refuse execution without aggregate resource limits."""

import asyncio
import shutil

import pytest

from src.core.codemode.sandbox import SandboxUnavailable, execute_python


def test_sandbox_fails_closed_when_systemd_run_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    which = shutil.which
    monkeypatch.setattr(shutil, 'which', lambda name: None if name == 'systemd-run' else which(name))
    with pytest.raises(SandboxUnavailable, match='systemd'):
        asyncio.run(execute_python("print('not executed')"))


def test_scope_limits_sandbox_descendants() -> None:
    code = """\
import subprocess
children = []
try:
    for i in range(40):
        try:
            children.append(subprocess.Popen(['/usr/bin/python3', '-c', 'import signal; signal.pause()']))
        except OSError:
            print('task limit enforced')
            break
    else:
        print('task limit not enforced')
finally:
    for child in children:
        child.terminate()
    for child in children:
        child.wait()
"""
    result = asyncio.run(execute_python(code, timeout_seconds=15))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'task limit enforced'


def test_sandbox_assigns_unique_named_systemd_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        real_spawn = asyncio.create_subprocess_exec
        units: list[str] = []

        async def capture(*args, **kwargs):
            for arg in args:
                if isinstance(arg, str) and arg.startswith('--unit='):
                    units.append(arg.removeprefix('--unit='))
            return await real_spawn(*args, **kwargs)

        monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)
        for _ in range(2):
            result = await execute_python("print('ok')")
            assert result.returncode == 0, result.stderr
        assert len(units) == 2
        assert units[0] != units[1]
        assert all(unit.startswith('sigil-code-') for unit in units)

    asyncio.run(run())


def test_timeout_drains_named_scope_without_killing_supervisor(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        real_spawn = asyncio.create_subprocess_exec
        launches: list[tuple[object, ...]] = []

        async def capture(*args, **kwargs):
            launches.append(args)
            return await real_spawn(*args, **kwargs)

        monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)
        result = await execute_python('while True: pass', timeout_seconds=1)
        assert result.timed_out
        scope = next(
            arg.removeprefix('--unit=')
            for args in launches
            for arg in args
            if isinstance(arg, str) and arg.startswith('--unit=')
        )
        query = await real_spawn(
            'systemctl',
            '--user',
            'show',
            '--property=TasksCurrent',
            '--value',
            scope,
            stdout=asyncio.subprocess.PIPE,
        )
        stdout, _ = await query.communicate()
        assert stdout.strip() in (b'0', b'[not set]')
        assert not any('kill' in args and '--kill-whom=all' in args for args in launches)

    asyncio.run(run())


def test_timeout_removes_scope_with_new_session_descendant(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        real_spawn = asyncio.create_subprocess_exec
        units: list[str] = []

        async def capture(*args, **kwargs):
            units.extend(
                arg.removeprefix('--unit=') for arg in args if isinstance(arg, str) and arg.startswith('--unit=')
            )
            return await real_spawn(*args, **kwargs)

        monkeypatch.setattr(asyncio, 'create_subprocess_exec', capture)
        code = "import subprocess\nsubprocess.Popen(['/usr/bin/python3','-c','while True: pass'], start_new_session=True)\nwhile True: pass"
        result = await asyncio.wait_for(execute_python(code, timeout_seconds=1), timeout=10)
        assert result.timed_out
        assert len(units) == 1
        proc = await real_spawn(
            'systemctl',
            '--user',
            'show',
            '--property=ControlGroup',
            '--value',
            units[0],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        assert proc.returncode == 0, stderr
        assert not stdout.strip()

    asyncio.run(run())


def test_cleanup_still_reaps_launcher_when_scope_signal_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.codemode import sandbox

    async def run() -> None:
        class Process:
            pid = 123456789
            stdin = None
            waited = False

            async def wait(self) -> int:
                self.waited = True
                return 0

        async def fail(unit: str) -> None:
            raise sandbox.SandboxUnavailable('scope unavailable')

        process = Process()
        monkeypatch.setattr(sandbox, '_signal_scope', fail)
        monkeypatch.setattr(sandbox, '_kill', lambda process: None)
        with pytest.raises(sandbox.SandboxUnavailable, match='scope unavailable'):
            await sandbox._cleanup_scope('test.scope', process)
        assert process.waited

    asyncio.run(run())


def test_repeated_cancellation_does_not_cancel_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.codemode import sandbox

    async def run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def cleanup(unit, process):
            started.set()
            await release.wait()
            finished.set()

        monkeypatch.setattr(sandbox, '_cleanup_scope_impl', cleanup)
        task = asyncio.create_task(sandbox._cleanup_scope('test.scope', object()))
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(run())


def test_cleanup_retries_scope_after_initial_signal_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.codemode import sandbox

    async def run() -> None:
        attempts = 0

        class Process:
            pid = 123456789
            stdin = None

            async def wait(self) -> int:
                return 0

        async def signal(unit: str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise sandbox.SandboxUnavailable('transient scope failure')

        monkeypatch.setattr(sandbox, '_signal_scope', signal)
        monkeypatch.setattr(sandbox, '_kill', lambda process: None)
        with pytest.raises(sandbox.SandboxUnavailable, match='transient scope failure'):
            await sandbox._cleanup_scope('test.scope', Process())
        assert attempts == 2

    asyncio.run(run())


def test_timeout_does_not_require_systemd_signalling(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.codemode import sandbox

    async def fail(unit: str) -> None:
        raise AssertionError('Healthy supervisor must not need scope signalling')

    monkeypatch.setattr(sandbox, '_signal_scope', fail)
    result = asyncio.run(execute_python('while True: pass', timeout_seconds=1))
    assert result.timed_out


def test_sandbox_rejects_unverifiable_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.codemode import sandbox

    def fail(group: str, unit: str) -> int:
        raise SandboxUnavailable('Cannot verify payload events')

    monkeypatch.setattr(sandbox, '_open_cgroup_events', fail)
    with pytest.raises(SandboxUnavailable, match='payload events'):
        asyncio.run(execute_python("print('must not execute')", timeout_seconds=5))


def test_cleanup_fails_when_scope_cannot_be_confirmed_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.core.codemode import sandbox

    async def run() -> None:
        class Process:
            pid = 123456789
            stdin = None

            async def wait(self) -> int:
                return 0

        async def signal(unit: str) -> None:
            return None

        async def verify(unit: str) -> None:
            raise sandbox.SandboxUnavailable('scope not empty')

        monkeypatch.setattr(sandbox, '_signal_scope', signal)
        monkeypatch.setattr(sandbox, '_verify_scope_empty', verify, raising=False)
        monkeypatch.setattr(sandbox, '_kill', lambda process: None)
        with pytest.raises(sandbox.SandboxUnavailable, match='scope not empty'):
            await sandbox._cleanup_scope('test.scope', Process())

    asyncio.run(run())


def test_cgroup_verification_uses_populated_and_rejects_unknown_hierarchy(tmp_path, monkeypatch) -> None:
    from src.core.codemode import sandbox

    monkeypatch.setattr(sandbox, '_CGROUP_ROOT', tmp_path)
    (tmp_path / 'cgroup.controllers').write_text('cpu memory pids')
    parent = tmp_path / 'user.slice'
    group = parent / 'sigil-code-test.scope'
    group.mkdir(parents=True)
    (group / 'cgroup.events').write_text('populated 0\nfrozen 0\n')
    sandbox._wait_for_empty_cgroup('/user.slice/sigil-code-test.scope', 'sigil-code-test.scope')
    with pytest.raises(SandboxUnavailable, match='hierarchy'):
        sandbox._wait_for_empty_cgroup('/missing/sigil-code-test.scope', 'sigil-code-test.scope')
    with pytest.raises(SandboxUnavailable, match='path'):
        sandbox._wait_for_empty_cgroup('/user.slice/../sigil-code-test.scope', 'sigil-code-test.scope')


def test_cgroup_wait_refuses_populated_or_missing_events(tmp_path, monkeypatch) -> None:
    from src.core.codemode import sandbox

    class Poll:
        def register(self, fd, events):
            pass

        def poll(self, timeout):
            return []

    monkeypatch.setattr(sandbox, '_CGROUP_ROOT', tmp_path)
    monkeypatch.setattr(sandbox.select, 'poll', Poll)
    (tmp_path / 'cgroup.controllers').write_text('cpu')
    group = tmp_path / 'user.slice' / 'sigil-code-test.scope'
    group.mkdir(parents=True)
    events = group / 'cgroup.events'
    events.write_text('populated 1\nfrozen 0\n')
    with pytest.raises(SandboxUnavailable, match='did not become empty'):
        sandbox._wait_for_empty_cgroup('/user.slice/sigil-code-test.scope', 'sigil-code-test.scope')
    events.unlink()
    with pytest.raises(SandboxUnavailable, match='event file'):
        sandbox._wait_for_empty_cgroup('/user.slice/sigil-code-test.scope', 'sigil-code-test.scope')


def test_cgroup_wait_rejects_unverified_missing_scope(tmp_path, monkeypatch) -> None:
    from src.core.codemode import sandbox

    monkeypatch.setattr(sandbox, '_CGROUP_ROOT', tmp_path)
    (tmp_path / 'cgroup.controllers').write_text('cpu')
    (tmp_path / 'user.slice').mkdir()
    with pytest.raises(SandboxUnavailable, match='unavailable|missing'):
        sandbox._wait_for_empty_cgroup('/user.slice/sigil-code-test.scope', 'sigil-code-test.scope')
