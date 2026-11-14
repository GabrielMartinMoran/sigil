"""Persistent diagnostics must not record exception messages or local values."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_records_frames_without_sensitive_exception_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.server.diagnostics import record

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path))
    try:
        raise RuntimeError('secret-token-and-conversation')
    except RuntimeError as error:
        record('server_failed', error=error, worker_count=2)
    files = list((tmp_path / 'service' / 'diagnostics').glob('*.jsonl'))
    assert len(files) == 1
    text = files[0].read_text()
    entry = json.loads(text)
    assert entry['event'] == 'server_failed'
    assert entry['error_type'] == 'RuntimeError'
    assert entry['frames'][-1]['function'] == 'test_records_frames_without_sensitive_exception_text'
    assert entry['worker_count'] == 2
    assert 'secret-token-and-conversation' not in text
    assert files[0].stat().st_mode & 0o777 == 0o600


def test_diagnostics_failure_does_not_interrupt_recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.server.diagnostics import record

    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('block')
    monkeypatch.setenv('SIGIL_HOME', str(blocked))
    record('recovery_failed', error=OSError('secret'))


def test_rotates_only_its_own_bounded_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.server import diagnostics

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path))
    monkeypatch.setattr(diagnostics, 'MAX_LOG_BYTES', 1)
    diagnostics.record('before')
    diagnostics.record('after')
    files = list((tmp_path / 'service' / 'diagnostics').glob('*.jsonl'))
    assert len(files) == 2
    entries = [json.loads(path.read_text())['event'] for path in files]
    assert sorted(entries) == ['after', 'before']


def test_diagnostics_refuse_symlink_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from src.server.diagnostics import record

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path))
    directory = tmp_path / 'service' / 'diagnostics'
    directory.mkdir(parents=True)
    outside = tmp_path / 'outside'
    outside.write_text('unchanged')
    (directory / f'process-{os.getpid()}.jsonl').symlink_to(outside)
    record('must_not_write')
    assert outside.read_text() == 'unchanged'


def test_uncaught_server_failure_survives_as_private_diagnostics(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    code = """
from src.server.diagnostics import diagnose_server
with diagnose_server():
    raise RuntimeError('sensitive contents')
"""
    completed = subprocess.run(
        [sys.executable, '-c', code],
        env={**os.environ, 'SIGIL_HOME': str(tmp_path)},
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert completed.returncode != 0
    lines = next((tmp_path / 'service' / 'diagnostics').glob('process-*.jsonl')).read_text()
    events = [json.loads(line) for line in lines.splitlines()]
    assert [event['event'] for event in events] == [
        'server_process_start',
        'server_process_exception',
        'server_process_exit',
    ]
    assert 'sensitive contents' not in lines
    assert events[1]['error_type'] == 'RuntimeError'


def test_fatal_python_signal_keeps_a_stack_without_a_core_dump(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    code = """
import faulthandler
import os
import resource
import signal
from src.server.diagnostics import diagnose_server
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
faulthandler.disable()
with diagnose_server():
    os.kill(os.getpid(), signal.SIGABRT)
"""
    completed = subprocess.run(
        [sys.executable, '-c', code],
        env={**os.environ, 'SIGIL_HOME': str(tmp_path)},
        capture_output=True,
        check=False,
        timeout=5,
    )
    assert completed.returncode < 0
    fault = next((tmp_path / 'service' / 'diagnostics').glob('fault-*.log'))
    assert 'Fatal Python error: Aborted' in fault.read_text()
    assert fault.stat().st_mode & 0o777 == 0o600


def test_framework_caught_startup_error_is_still_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from src.server.app import create_app
    from src.server.config import ServerConfig
    from tests.test_server_endpoint import _RuntimeStub

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path))
    runtime = _RuntimeStub()

    def broken_start() -> None:
        raise RuntimeError('private startup detail')

    monkeypatch.setattr(runtime, 'start', broken_start)
    app = create_app(runtime, ServerConfig(password='test-only'), dist=tmp_path / 'missing')
    with pytest.raises(RuntimeError, match='private startup detail'), TestClient(app):
        pass
    text = next((tmp_path / 'service' / 'diagnostics').glob('process-*.jsonl')).read_text()
    entry = json.loads(text)
    assert entry['event'] == 'server_lifespan_failed'
    assert entry['error_type'] == 'RuntimeError'
    assert 'private startup detail' not in text


def test_signal_record_can_reenter_logging_without_deadlocking(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    code = """
from src.server import diagnostics
with diagnostics._lock:
    diagnostics.record('server_signal_received', signal=15)
"""
    subprocess.run(
        [sys.executable, '-c', code],
        env={**os.environ, 'SIGIL_HOME': str(tmp_path)},
        capture_output=True,
        check=True,
        timeout=5,
    )
    entry = json.loads(next((tmp_path / 'service' / 'diagnostics').glob('process-*.jsonl')).read_text())
    assert entry['event'] == 'server_signal_received'
