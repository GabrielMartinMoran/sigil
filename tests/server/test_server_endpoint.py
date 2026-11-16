"""The rendezvous file: how a client finds the service, and why it trusts so little.

Two properties are load-bearing and both are tested here rather than assumed:

- **The file is private and complete.** It holds a per-run token, so the
  directory is ``0700`` and the file is ``0600`` *written explicitly*, because a
  mode that depends on the caller's umask is a mode that depends on the shell.
  It is replaced with ``os.replace``, so a reader either sees the previous
  publication or the next one, never half of either.
- **Nothing in the file is trusted.** A stale file, a truncated one, a symlink,
  a file another user can read: all of them are ignored. A client that believed
  its contents would send a token to whatever stale port happens to be there.

The last test in this file pins the ordering the plan asks for: the port is
listening before the file that names it exists.
"""

from __future__ import annotations

import json
import os
import socket
import threading
from pathlib import Path

import pytest

from src.core.config.paths import endpoint_file, service_dir
from src.server.cli import listening_socket
from src.server.launch.endpoint import Endpoint
from src.server.launch.endpoint_file import read_endpoint
from src.server.launch.endpoint_publisher import EndpointPublisher
from tests.support.server_endpoint import _RuntimeStub, _umask


def test_the_service_directory_sits_under_the_sigil_home(tmp_path: Path) -> None:
    environ = {'SIGIL_HOME': str(tmp_path)}

    assert service_dir(environ) == tmp_path / 'service'
    assert endpoint_file(environ) == tmp_path / 'service' / 'endpoint.json'


def test_the_service_directory_can_be_moved_with_an_environment_override(tmp_path: Path) -> None:
    """Same convention as the other directories: one variable, and no patching."""

    environ = {'SIGIL_SERVICE_DIR': str(tmp_path / 'run')}

    assert service_dir(environ) == tmp_path / 'run'
    assert endpoint_file(environ) == tmp_path / 'run' / 'endpoint.json'


def test_publishing_records_the_contract_the_client_reads(tmp_path: Path) -> None:
    path = tmp_path / 'service' / 'endpoint.json'
    publisher = EndpointPublisher(path=path, clock=lambda: 1000.5)

    published = publisher.publish(host='127.0.0.1', port=8760)

    assert json.loads(path.read_text(encoding='utf-8')) == {
        'host': '127.0.0.1',
        'port': 8760,
        'pid': os.getpid(),
        'token': publisher.token,
        'started_at': 1000.5,
    }
    assert published == Endpoint(host='127.0.0.1', port=8760, pid=os.getpid(), token=publisher.token, started_at=1000.5)
    assert read_endpoint(path) == published


def test_the_directory_and_the_file_are_private_under_a_permissive_umask(tmp_path: Path) -> None:
    """0700 and 0600 are written down; the umask is allowed to make them no wider."""

    path = tmp_path / 'service' / 'endpoint.json'
    with _umask(0o022):
        EndpointPublisher(path=path).publish(host='127.0.0.1', port=8760)

    assert _mode(path.parent) == 0o700
    assert _mode(path) == 0o600


def test_an_existing_world_readable_directory_is_narrowed(tmp_path: Path) -> None:
    """A token in a directory others can list is not a secret, whoever created it."""

    path = tmp_path / 'service' / 'endpoint.json'
    path.parent.mkdir(mode=0o755)
    os.chmod(path.parent, 0o755)

    EndpointPublisher(path=path).publish(host='127.0.0.1', port=8760)

    assert _mode(path.parent) == 0o700


def test_the_token_rotates_with_every_run(tmp_path: Path) -> None:
    """A token that survives a restart is a token that leaked once and stayed valid."""

    first = EndpointPublisher(path=tmp_path / 'a.json')
    second = EndpointPublisher(path=tmp_path / 'b.json')

    assert first.token != second.token
    assert len(first.token) >= 32


def test_publishing_replaces_the_file_atomically(tmp_path: Path) -> None:
    path = tmp_path / 'service' / 'endpoint.json'
    publisher = EndpointPublisher(path=path)
    publisher.publish(host='127.0.0.1', port=8000)
    inode = os.lstat(path).st_ino

    publisher.publish(host='127.0.0.1', port=8001)

    assert os.lstat(path).st_ino != inode
    assert read_endpoint(path) is not None
    assert read_endpoint(path).port == 8001
    assert [entry.name for entry in path.parent.iterdir()] == ['endpoint.json']


def test_a_reader_never_sees_a_half_written_file(tmp_path: Path) -> None:
    path = tmp_path / 'service' / 'endpoint.json'
    publisher = EndpointPublisher(path=path)
    publisher.publish(host='127.0.0.1', port=8000)
    stop = threading.Event()
    misses: list[bool] = []

    def watch() -> None:
        while not stop.is_set():
            if read_endpoint(path) is None:
                misses.append(True)

    watcher = threading.Thread(target=watch, name='endpoint-reader')
    watcher.start()
    try:
        for port in range(8000, 8030):
            publisher.publish(host='127.0.0.1', port=port)
    finally:
        stop.set()
        watcher.join(timeout=10)

    assert misses == []


@pytest.mark.parametrize(
    'content',
    [
        '{not json',
        '[]',
        json.dumps({'host': '127.0.0.1', 'port': 8760}),
        json.dumps({'host': '127.0.0.1', 'port': '8760', 'pid': 1, 'token': 'x', 'started_at': 1.0}),
        json.dumps({'host': '127.0.0.1', 'port': 0, 'pid': 1, 'token': 'x', 'started_at': 1.0}),
        json.dumps({'host': '', 'port': 8760, 'pid': 1, 'token': 'x', 'started_at': 1.0}),
        json.dumps({'host': '127.0.0.1', 'port': 8760, 'pid': 1, 'token': '', 'started_at': 1.0}),
        json.dumps({'host': '127.0.0.1', 'port': 8760, 'pid': 1, 'token': 'x', 'started_at': 'now'}),
    ],
)
def test_a_corrupt_file_is_ignored_and_not_partially_trusted(tmp_path: Path, content: str) -> None:
    path = tmp_path / 'service' / 'endpoint.json'
    path.parent.mkdir(mode=0o700)
    path.write_text(content, encoding='utf-8')
    os.chmod(path, 0o600)

    assert read_endpoint(path) is None


def test_a_file_another_user_could_read_is_refused(tmp_path: Path) -> None:
    """The mode is part of the secret: 0644 means the token is already public."""

    path = tmp_path / 'service' / 'endpoint.json'
    path.parent.mkdir(mode=0o700)
    path.write_text(
        json.dumps({'host': '127.0.0.1', 'port': 8760, 'pid': 1, 'token': 'secret', 'started_at': 1.0}),
        encoding='utf-8',
    )
    os.chmod(path, 0o644)

    assert read_endpoint(path) is None


def test_a_symlink_is_not_followed(tmp_path: Path) -> None:
    """A link is how another path becomes this path; a link is refused, not resolved."""

    real = tmp_path / 'elsewhere.json'
    real.write_text(
        json.dumps({'host': '127.0.0.1', 'port': 8760, 'pid': 1, 'token': 'secret', 'started_at': 1.0}),
        encoding='utf-8',
    )
    os.chmod(real, 0o600)
    path = tmp_path / 'service' / 'endpoint.json'
    path.parent.mkdir(mode=0o700)
    path.symlink_to(real)

    assert read_endpoint(path) is None


def test_a_missing_file_is_simply_absent(tmp_path: Path) -> None:
    assert read_endpoint(tmp_path / 'service' / 'endpoint.json') is None


def test_clearing_removes_the_file_and_is_safe_to_repeat(tmp_path: Path) -> None:
    path = tmp_path / 'service' / 'endpoint.json'
    publisher = EndpointPublisher(path=path)
    publisher.publish(host='127.0.0.1', port=8760)

    publisher.clear()
    publisher.clear()

    assert not path.exists()
    assert read_endpoint(path) is None


def test_clearing_leaves_another_runs_file_alone(tmp_path: Path) -> None:
    """A dying process must not unpublish the service that replaced it."""

    path = tmp_path / 'service' / 'endpoint.json'
    previous = EndpointPublisher(path=path)
    previous.publish(host='127.0.0.1', port=8000)
    current = EndpointPublisher(path=path)
    current.publish(host='127.0.0.1', port=8001)

    previous.clear()

    published = read_endpoint(path)
    assert published is not None
    assert published.token == current.token


def test_the_app_publishes_when_it_starts_and_unpublishes_when_it_stops(tmp_path: Path) -> None:
    """The file exists only while the service behind it does."""

    from fastapi.testclient import TestClient

    from src.server.api.app import create_app
    from src.server.launch.server_config import ServerConfig

    path = tmp_path / 'service' / 'endpoint.json'
    publisher = EndpointPublisher(path=path)
    runtime = _RuntimeStub()
    app = create_app(
        runtime,  # type: ignore[arg-type] - a stub keeps this a lifecycle test
        ServerConfig(host='127.0.0.1', port=8760, password='secret-pass'),
        dist=tmp_path / 'missing',
        publisher=publisher,
    )

    with TestClient(app):
        published = read_endpoint(path)
        assert published is not None
        assert (published.host, published.port) == ('127.0.0.1', 8760)
        assert published.token == publisher.token

    assert read_endpoint(path) is None
    runtime.session_runtime.aclose.assert_awaited_once()
    assert runtime.calls[-1] == 'close'


def test_the_port_is_listening_before_the_endpoint_is_published() -> None:
    """The ordering, proved where it matters: a connection succeeds with no server loop."""

    sock = listening_socket('127.0.0.1', 0)
    try:
        host, port = sock.getsockname()[:2]
        with socket.create_connection((host, port), timeout=5):
            pass
    finally:
        sock.close()


def test_lifespan_releases_runtime_when_session_shutdown_fails(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from src.server.api.app import create_app
    from src.server.launch.server_config import ServerConfig

    runtime = _RuntimeStub()
    runtime.session_runtime.aclose.side_effect = RuntimeError('shutdown failed')
    app = create_app(
        runtime,  # type: ignore[arg-type]
        ServerConfig(host='127.0.0.1', port=8760, password='secret-pass'),
        dist=tmp_path / 'missing',
    )
    with pytest.raises(RuntimeError, match='shutdown failed'), TestClient(app):
        pass
    runtime.composition.extensions.close.assert_awaited_once()
    assert runtime.calls[-1] == 'close'


def _mode(path: Path) -> int:
    """Permission bits of one path."""

    return path.stat().st_mode & 0o777
