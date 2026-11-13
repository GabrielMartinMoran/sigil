"""Finding a live service, or starting exactly one, is a race with a witness.

Two levels prove it, and the level is chosen per behaviour:

- **Functions, with no child process.** `probe` and `live_endpoint` are the
  discovery primitives: a short HTTP request and the pid check. They are tested
  against a real socket in this process, because the failure they must detect is
  a real one (closed port, silent port, ``500``) and a mock would prove nothing
  about the timeout.
- **A real process where the arbitration is the behaviour.** The ownership lock
  is an OS fact, so the race, the death of a winner and the deadline are tested
  with real processes. Two processes competing and one attaching is not
  expressible in one process.

**The server process is a double on purpose.** Starting the real `sigil server`
means a `Composition` (settings, MCP config, skill registry) behind a real
`uvicorn`; worse, `main.py` refuses to start when `SIGIL_SELF_GUARD` is set,
which is exactly the environment this suite may run in. So the child the launcher
spawns is a small script that does the three things that matter — take the same
ownership lock, publish the same endpoint file, answer `/health` over a real
socket — and testing does not depend on that guard being absent. Nothing about
the launcher is mocked: the lock, the endpoint file, the HTTP probe and the child
process are all real. What is not covered here is the composition of the engine
behind the endpoint, which `tests/test_server_runtime.py` and `test_server_queue.py`
already own.
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from src.server.cli import listening_socket
from src.server.endpoint import Endpoint, read_endpoint
from src.server.launch import (
    LaunchCommand,
    ServiceDied,
    ServiceStarting,
    ServiceUnpublished,
    default_command,
    ensure_service,
    live_endpoint,
    probe,
    spawn_server,
)
from src.server.ownership import OwnershipError, ServerOwnership

ROOT = Path(__file__).resolve().parents[1]
"""The checkout, so a child script can import `src.*` without `PYTHONPATH`."""

DOUBLE = """
import json
import sys
import time

sys.path.insert(0, {root})

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from src.server.endpoint import EndpointPublisher
from src.server.ownership import OwnershipError, ServerOwnership


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({{'status': 'ok'}}).encode()
        self.send_response(200 if self.path == '/health' else 404)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    mode, database = sys.argv[1], sys.argv[2]
    budget = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0
    try:
        # The same lock, on the same path, as the real server: this is the fact
        # the launcher arbitrates on, so it must not be simulated.
        ServerOwnership(database).acquire()
    except OwnershipError as error:
        print('LOST ' + str(error), file=sys.stderr, flush=True)
        return 3
    print('LOCKED', flush=True)
    if mode == 'hold':
        # A winner that dies on its way in: it owns the store, publishes nothing,
        # and exits. The launcher must notice rather than wait out its deadline.
        if budget:
            time.sleep(budget)
            print('DIED', flush=True)
            return 4
        while True:
            time.sleep(0.1)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Health)
    EndpointPublisher().publish(host='127.0.0.1', port=server.server_address[1])
    print('PUBLISHED', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    sys.exit(main())
"""

COMPETITOR = """
import json
import sys
import time

sys.path.insert(0, {root})

from pathlib import Path

from src.server.launch import LaunchCommand, ensure_service

gate = Path({gate})
while not gate.exists():
    time.sleep(0.01)

command = LaunchCommand(argv=({python}, {script}, 'serve', {database}))
try:
    launch = ensure_service(
        database=Path({database}),
        command=command,
        endpoint_path=Path({endpoint}),
        wait=60,
    )
except Exception as error:
    print(json.dumps({{'error': type(error).__name__ + ': ' + str(error)}}), flush=True)
else:
    print(
        json.dumps(
            {{
                'launched': launch.launched,
                'port': launch.endpoint.port,
                'token': launch.endpoint.token,
                'pid': launch.endpoint.pid,
            }}
        ),
        flush=True,
    )
"""


# --- helpers ---------------------------------------------------------------


def _wait_for(predicate: Callable[[], Any], *, timeout: float = 30.0) -> Any:
    """Waits for a real event with a bounded deadline. No fixed sleep as a guess."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f'The condition did not hold within {timeout:.0f}s')


def _script(tmp_path: Path) -> Path:
    path = tmp_path / 'double.py'
    path.write_text(DOUBLE.format(root=repr(str(ROOT))), encoding='utf-8')
    return path


def _double(tmp_path: Path, mode: str, database: Path) -> LaunchCommand:
    """The launcher's command, aimed at the process double instead of `main.py`."""

    script = _script(tmp_path)
    return LaunchCommand(argv=(sys.executable, str(script), mode, str(database)))


def _service_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Points this process *and its children* at one service directory."""

    directory = tmp_path / 'service'
    monkeypatch.setenv('SIGIL_SERVICE_DIR', str(directory))
    return directory


def _dead_pid() -> int:
    """A pid that is certainly gone, because this process watched it exit."""

    process = subprocess.Popen([sys.executable, '-c', 'pass'])
    process.wait(timeout=30)
    return process.pid


def _closed_port() -> int:
    """A port nothing listens on: bound, read, then closed again."""

    sock = listening_socket('127.0.0.1', 0)
    try:
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _write_endpoint(path: Path, endpoint: Endpoint) -> None:
    """A valid file, written the way a previous run would have left it."""

    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(endpoint.to_payload()), encoding='utf-8')
    os.chmod(path, 0o600)


def _running(script: Path) -> list[int]:
    """The pids whose command line names this test's script.

    A server started by the launcher is **not** this process's child: the competitor
    started it, and `start_new_session=True` put it in a session of its own, so it
    survives both its parent and this test. Counting it by name is the only witness
    that a run left nothing behind — a `Popen` handle would not see it, and neither
    would an exit code.

    Zombies are not counted: their command line is empty, and a reaped process is not
    a running one.
    """

    marker = str(script).encode()
    pids: list[int] = []
    if not Path('/proc').exists():
        return pids
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            command = (entry / 'cmdline').read_bytes()
        except OSError:
            continue
        if marker in command:
            pids.append(int(entry.name))
    return pids


def _gone(script: Path, *, timeout: float) -> bool:
    """Waits for the script's processes to disappear; ``False`` at the deadline."""

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _running(script):
            return True
        time.sleep(0.02)
    return not _running(script)


_LAUNCHED: dict[int, Path] = {}
"""Every double this module started, by pid, so no exit path can strand one.

A ``finally`` covers an assertion, an exception and a timeout. It does not cover a
worker killed mid-test — ``-x`` teardown, SIGKILL, the OOM killer — which is how the
orphans this module used to leave were born. Nothing here can survive a SIGKILL of this
process either; what it does cover is every exit that runs Python at all.
"""


def _watch(pid: int, script: Path) -> int:
    """Remembers a started double so `_stop_leftovers` can stop it."""

    if pid:
        _LAUNCHED[pid] = script
    return pid


@atexit.register
def _stop_leftovers() -> None:
    """Last line of defence: stops anything a failed test could not."""

    for pid, script in list(_LAUNCHED.items()):
        _stop_server(script, pid, timeout=5.0)


def _published_pid(endpoint_path: Path) -> int:
    """The pid the published endpoint names, or ``0`` when nothing was published.

    Read in `finally`, not only from the winner's report: a test that fails before it
    reads the report still started a server, and the pid is what its process group is
    addressed by. The read is retried while the file exists but does not parse, because
    a run replaces that file atomically — the one moment a pid is most needed is the
    one moment a single read can land mid-replace.
    """

    if not endpoint_path.exists():
        return 0
    for attempt in range(25):
        published = read_endpoint(endpoint_path)
        if published is not None:
            return published.pid
        if attempt < 24:
            time.sleep(0.04)
    return 0


def _stop_server(script: Path, *pids: int, timeout: float = 10.0) -> None:
    """Stops launcher-started servers, group and all, and waits until they are gone.

    ``killpg`` and not ``kill``: the child leads its own process group
    (``start_new_session=True``), so signalling the group is what reaches it even when
    the intermediate competitor process is already gone. SIGKILL is the escalation,
    because a leaked ``double.py serve`` outlives the run and holds a port and the
    ownership lock that the next run then finds taken.

    ``*pids`` because no single source is trustworthy: the endpoint file can be mid-
    replace, and a `Child` handle is only available on the path that returned one. The
    callers pass every handle they have and this stops whatever it finds.
    """

    deadline = time.monotonic() + timeout
    known = {pid for pid in pids if pid}
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        while True:
            # Re-scanned on every round, not just remembered from entry: a candidate can
            # be between fork and exec when the caller's pid is taken, and until it execs
            # its command line is still the competitor's — so a scan misses it and a
            # fixed pid list would leave exactly that process running. This is the race
            # that kept one `double.py serve` alive under `-n 8` while a focused run
            # cleaned up perfectly.
            known |= set(_running(script))
            for pid in known:
                try:
                    os.killpg(pid, signal_number)
                except ProcessLookupError:
                    pass
            if _gone(script, timeout=0.4):
                for pid in known:
                    _LAUNCHED.pop(pid, None)
                return
            if time.monotonic() >= deadline:
                break


class _HealthServer:
    """An HTTP server in this process: enough to answer `/health`, and no more."""

    def __init__(self, status: int = 200) -> None:
        self.seen: list[str] = []
        seen = self.seen
        status_code = status

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                seen.append(self.headers.get('Authorization', ''))
                body = b'{"status": "ok"}'
                self.send_response(status_code if self.path == '/health' else 404)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    def endpoint(self, *, pid: int | None = None, token: str = 'run-token') -> Endpoint:
        return Endpoint(
            host='127.0.0.1',
            port=self.port,
            pid=os.getpid() if pid is None else pid,
            token=token,
            started_at=1.0,
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def health() -> Any:
    server = _HealthServer()
    try:
        yield server
    finally:
        server.close()


# --- discovery: a short request, a short deadline ---------------------------


def test_a_probe_answers_yes_and_sends_the_run_token(health: _HealthServer) -> None:
    """Liveness is a real request; the token travels so the answer is about this run."""

    assert probe(health.endpoint(), timeout=2.0) is True
    assert health.seen == ['Bearer run-token']


def test_a_probe_answers_no_when_nothing_listens() -> None:
    endpoint = Endpoint(host='127.0.0.1', port=_closed_port(), pid=os.getpid(), token='t', started_at=1.0)

    started_at = time.monotonic()

    assert probe(endpoint, timeout=0.5) is False
    assert time.monotonic() - started_at < 5


def test_a_probe_answers_no_when_the_port_accepts_and_says_nothing() -> None:
    """A bound socket with nobody accepting is the case a bare connect would pass."""

    sock = listening_socket('127.0.0.1', 0)
    try:
        endpoint = Endpoint(
            host='127.0.0.1',
            port=int(sock.getsockname()[1]),
            pid=os.getpid(),
            token='t',
            started_at=1.0,
        )
        started_at = time.monotonic()

        assert probe(endpoint, timeout=0.2) is False
        assert time.monotonic() - started_at < 3
    finally:
        sock.close()


def test_a_probe_answers_no_when_health_is_not_ok() -> None:
    server = _HealthServer(status=500)
    try:
        assert probe(server.endpoint(), timeout=2.0) is False
    finally:
        server.close()


def test_a_missing_endpoint_is_not_a_live_endpoint(tmp_path: Path) -> None:
    assert live_endpoint(tmp_path / 'service' / 'endpoint.json', timeout=0.2) is None


def test_a_file_whose_process_is_gone_is_not_a_live_endpoint(tmp_path: Path, health: _HealthServer) -> None:
    """The port answers, the pid does not: this is another run's file, or a stale one."""

    path = tmp_path / 'service' / 'endpoint.json'
    _write_endpoint(path, health.endpoint(pid=_dead_pid()))

    assert live_endpoint(path, timeout=2.0) is None


def test_a_file_whose_process_answers_is_a_live_endpoint(tmp_path: Path, health: _HealthServer) -> None:
    path = tmp_path / 'service' / 'endpoint.json'
    published = health.endpoint()
    _write_endpoint(path, published)

    assert live_endpoint(path, timeout=2.0) == published


def test_a_live_process_with_a_silent_port_is_not_a_live_endpoint(tmp_path: Path) -> None:
    path = tmp_path / 'service' / 'endpoint.json'
    _write_endpoint(
        path,
        Endpoint(host='127.0.0.1', port=_closed_port(), pid=os.getpid(), token='t', started_at=1.0),
    )

    assert live_endpoint(path, timeout=0.2) is None


# --- the lock as the only arbiter ------------------------------------------


def test_the_store_is_reported_free_and_left_free(tmp_path: Path) -> None:
    """The probe holds nothing when it returns: the child takes the lock for real."""

    database = tmp_path / 'sigil.db'

    assert ServerOwnership(database).free() is True

    later = ServerOwnership(database)
    later.acquire()
    later.release()


def test_the_store_is_reported_taken_while_someone_holds_it(tmp_path: Path) -> None:
    database = tmp_path / 'sigil.db'
    holder = ServerOwnership(database)
    holder.acquire()
    try:
        assert ServerOwnership(database).free() is False
    finally:
        holder.release()


def test_asking_whether_the_store_is_free_leaves_the_owner_record_alone(tmp_path: Path) -> None:
    """A question must not overwrite the answer: the record names the real holder.

    The launcher reads that record to tell a dead owner from one that is between two
    steps, so a probe that recorded its own pid would destroy the evidence it needs —
    which is exactly the bug this test pins.
    """

    database = tmp_path / 'sigil.db'
    probe = ServerOwnership(database)

    assert probe.free() is True
    assert probe.owner_pid() is None

    holder = ServerOwnership(database)
    holder.acquire()
    try:
        assert ServerOwnership(database).free() is False
        assert probe.owner_pid() == os.getpid()
    finally:
        holder.release()


# --- how the child is launched --------------------------------------------


def test_the_child_runs_in_its_own_session_without_inherited_descriptors(tmp_path: Path) -> None:
    """Detached from the terminal, and not handed the launcher's descriptors."""

    read_fd, write_fd = os.pipe()
    try:
        command = LaunchCommand(
            argv=(
                sys.executable,
                '-c',
                (
                    'import json, os, sys; print(json.dumps({"sid": os.getsid(0), '
                    '"fds": sorted(os.listdir("/proc/self/fd"))})); sys.stderr.write("boom")'
                ),
            )
        )
        child = spawn_server(command)
        try:
            assert child.wait(timeout=30) == 0
            report = json.loads(child.output_tail().splitlines()[0])
            assert report['sid'] != os.getsid(0)
            if Path('/proc/self/fd').exists():
                assert str(write_fd) not in report['fds']
            assert 'boom' in child.output_tail()
        finally:
            child.close()
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_the_default_command_re_executes_this_interpreter_and_main_py() -> None:
    command = default_command(workspace=Path('/tmp/workspace'))

    assert command.argv[:3] == (sys.executable, str(ROOT / 'main.py'), 'server')
    assert command.argv[3:] == ('--workspace', '/tmp/workspace')
    # Nothing is stripped from the environment: the self-guard stays in force.
    assert command.env is None


def test_the_default_command_starts_the_api_backend_without_the_web_client() -> None:
    """The TUI launches the API: no bundle to check, no password to print, no page.

    `server` is the backend surface, so a web flag here would publish a password the
    terminal never shows and mount a client nobody asked for.
    """
    command = default_command()

    assert command.argv[2] == 'server'
    assert not any(flag in command.argv for flag in ('--password', '--rebuild-ui'))


# --- the launcher, with real processes ------------------------------------


def test_the_first_caller_launches_and_the_next_one_attaches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = tmp_path / 'sigil.db'
    endpoint_path = _service_dir(tmp_path, monkeypatch) / 'endpoint.json'
    command = _double(tmp_path, 'serve', database)

    first = ensure_service(database=database, command=command, endpoint_path=endpoint_path, wait=30)
    try:
        assert first.launched is True
        assert first.child is not None and first.child.alive
        assert read_endpoint(endpoint_path) == first.endpoint
        assert probe(first.endpoint, timeout=2.0) is True

        second = ensure_service(database=database, command=command, endpoint_path=endpoint_path, wait=30)
        assert second.launched is False
        assert second.child is None
        assert second.endpoint == first.endpoint
    finally:
        assert first.child is not None
        first.child.terminate()
        first.child.close()


def test_a_stale_endpoint_is_ignored_and_replaced_by_the_new_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file left by SIGKILL is valid JSON about a port that is gone."""

    database = tmp_path / 'sigil.db'
    endpoint_path = _service_dir(tmp_path, monkeypatch) / 'endpoint.json'
    _write_endpoint(
        endpoint_path,
        Endpoint(host='127.0.0.1', port=_closed_port(), pid=_dead_pid(), token='stale-token', started_at=1.0),
    )

    launch = ensure_service(
        database=database, command=_double(tmp_path, 'serve', database), endpoint_path=endpoint_path, wait=30
    )
    try:
        assert launch.launched is True
        assert launch.endpoint.token != 'stale-token'
        assert launch.endpoint.pid == (launch.child.pid if launch.child else 0)
        assert read_endpoint(endpoint_path) == launch.endpoint
    finally:
        assert launch.child is not None
        launch.child.terminate()
        launch.child.close()


def test_a_failed_launch_leaves_another_runs_endpoint_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing here deletes a file it does not own: the dead run's file stays as it was."""

    database = tmp_path / 'sigil.db'
    endpoint_path = _service_dir(tmp_path, monkeypatch) / 'endpoint.json'
    _write_endpoint(
        endpoint_path,
        Endpoint(host='127.0.0.1', port=_closed_port(), pid=_dead_pid(), token='stale', started_at=1.0),
    )
    before = endpoint_path.read_bytes()
    inode = os.lstat(endpoint_path).st_ino
    refusing = LaunchCommand(argv=(sys.executable, '-c', 'import sys; sys.stderr.write("no engine here"); sys.exit(3)'))

    with pytest.raises(ServiceDied) as info:
        ensure_service(database=database, command=refusing, endpoint_path=endpoint_path, wait=15)

    assert 'no engine here' in str(info.value)
    assert endpoint_path.read_bytes() == before
    assert os.lstat(endpoint_path).st_ino == inode


def test_two_processes_never_start_two_services(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point: one winner, one server, and the other one attaches to it."""

    database = tmp_path / 'sigil.db'
    service = _service_dir(tmp_path, monkeypatch)
    endpoint_path = service / 'endpoint.json'
    gate = tmp_path / 'gate'
    server = _script(tmp_path)
    # Nothing of this test's double exists yet: the count taken afterwards is what
    # turns "cleanup ran" into "nothing was left running".
    assert _running(server) == []
    script = tmp_path / 'competitor.py'
    script.write_text(
        COMPETITOR.format(
            root=repr(str(ROOT)),
            gate=repr(str(gate)),
            python=repr(sys.executable),
            script=repr(str(server)),
            database=repr(str(database)),
            endpoint=repr(str(endpoint_path)),
        ),
        encoding='utf-8',
    )
    processes = [
        subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    server_pid = 0
    try:
        gate.touch()
        reports = []
        for process in processes:
            out, err = process.communicate(timeout=90)
            assert process.returncode == 0, err
            reports.append(json.loads(out.strip().splitlines()[-1]))
        assert [report for report in reports if 'error' in report] == []
        winners = [report for report in reports if report['launched']]
        assert len(winners) == 1
        assert len({(report['port'], report['token']) for report in reports}) == 1
        server_pid = winners[0]['pid']
        _watch(server_pid, server)
        assert read_endpoint(endpoint_path).pid == server_pid  # type: ignore[union-attr]
        assert probe(read_endpoint(endpoint_path), timeout=2.0) is True  # type: ignore[arg-type]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            # Reaped either way: a child of ours that exited and is never waited on
            # stays a zombie for the rest of the session.
            process.wait(timeout=30)
        # The server is *not* our child — the competitor started it, detached into its
        # own session — so killing the competitor above leaves it running. That is what
        # leaked: a failed assertion before the winner was read left `double.py serve`
        # alive for hours. The pid comes from the report when there was one, and from
        # the published endpoint otherwise.
        _stop_server(server, server_pid, _published_pid(endpoint_path))
        assert _running(server) == [], 'the run left a service process behind'


def test_a_winner_dying_before_publication_is_a_typed_error_and_the_retry_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No hang: the launcher notices the owner is gone and says so."""

    database = tmp_path / 'sigil.db'
    endpoint_path = _service_dir(tmp_path, monkeypatch) / 'endpoint.json'
    server = _script(tmp_path)
    # Nothing of this test's double is running yet: the count afterwards is the proof.
    assert _running(server) == []
    holder = subprocess.Popen(
        [sys.executable, str(server), 'hold', str(database), '1.0'],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        assert holder.stdout is not None
        assert _wait_for(lambda: holder.stdout.readline().strip() == 'LOCKED', timeout=60)

        started_at = time.monotonic()
        with pytest.raises(ServiceDied) as info:
            ensure_service(
                database=database,
                command=_double(tmp_path, 'serve', database),
                endpoint_path=endpoint_path,
                wait=30,
            )
        assert time.monotonic() - started_at < 20
        assert 'without publishing' in str(info.value)
    finally:
        holder.kill()
        holder.wait(timeout=30)
        # The losing candidate normally spawns nothing — the holder owns the store —
        # but the race can land the other way on a fast machine. Then `ensure_service`
        # did start a real double and `pytest.raises` failed before anything could stop
        # it, and a `double.py serve` detached into its own session outlives this run.
        # The published file is the only handle to it once the child object is lost.
        _stop_server(server, _published_pid(endpoint_path))
        assert _gone(server, timeout=10), 'the failed attempt left a service process behind'

    retry = ensure_service(
        database=database, command=_double(tmp_path, 'serve', database), endpoint_path=endpoint_path, wait=30
    )
    try:
        assert retry.launched is True
        assert probe(retry.endpoint, timeout=2.0) is True
    finally:
        assert retry.child is not None
        # Read before `close`: both handles matter, the child this call was given and
        # the published file, and the object stops being the way to reach it.
        child_pid = retry.child.pid
        retry.child.terminate()
        retry.child.close()
        _stop_server(server, child_pid, _published_pid(endpoint_path))
        assert _gone(server, timeout=10), 'the run left a service process behind'


def test_a_child_that_never_publishes_reports_the_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A wedged start is reported at the deadline, with the handle to stop it."""

    database = tmp_path / 'sigil.db'
    endpoint_path = _service_dir(tmp_path, monkeypatch) / 'endpoint.json'

    started_at = time.monotonic()
    with pytest.raises(ServiceUnpublished) as info:
        ensure_service(
            database=database,
            command=_double(tmp_path, 'hold', database),
            endpoint_path=endpoint_path,
            wait=1.0,
            interval=0.05,
        )
    assert time.monotonic() - started_at < 15
    assert 'no endpoint' in str(info.value)

    child = info.value.child
    try:
        assert child.alive is True
    finally:
        child.terminate()
        child.close()


def test_a_holder_that_never_publishes_is_reported_as_starting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Someone else owns the store: the loser says so instead of waiting forever."""

    database = tmp_path / 'sigil.db'
    endpoint_path = _service_dir(tmp_path, monkeypatch) / 'endpoint.json'
    holder = ServerOwnership(database)
    holder.acquire()
    try:
        with pytest.raises(ServiceStarting) as info:
            ensure_service(
                database=database,
                command=_double(tmp_path, 'serve', database),
                endpoint_path=endpoint_path,
                wait=1.0,
                interval=0.05,
            )
    finally:
        holder.release()

    assert 'starting' in str(info.value)
    assert not endpoint_path.exists()


def test_sigil_server_refuses_a_taken_store_before_building_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`sigil server` asks the lock first, so a losing candidate pays nothing else."""

    from src.server import cli, webbuild

    monkeypatch.setenv('SIGIL_HOME', str(tmp_path))
    # The bundle check runs before the lock and is about the checkout, not this
    # behaviour, so it is answered as "fresh" instead of depending on web/dist.
    monkeypatch.setattr(webbuild, 'staleness', lambda *args, **kwargs: webbuild.Staleness(stale=False))

    class _Engine:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError('the engine was composed for a candidate that cannot serve')

    monkeypatch.setattr(cli, 'Composition', _Engine)
    holder = ServerOwnership(tmp_path / 'sigil.db')
    holder.acquire()
    try:
        code = cli.run(['--port', '8761', '--password', 'password123'])
    finally:
        holder.release()

    assert code == 2
    assert f'{tmp_path / "sigil.db"}.server.lock' in capsys.readouterr().err


def test_a_broken_lock_path_is_an_error_and_not_a_race(tmp_path: Path) -> None:
    """An unsafe lock path can never be published by anybody, so it is not waited on."""

    database = tmp_path / 'sigil.db'
    database.with_name(f'{database.name}.server.lock').symlink_to(tmp_path / 'elsewhere')

    with pytest.raises(OwnershipError, match='not a safe regular file'):
        ensure_service(database=database, endpoint_path=tmp_path / 'endpoint.json', wait=0.2)
