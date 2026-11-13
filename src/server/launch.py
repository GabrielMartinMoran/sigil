"""Finding the service that already runs, or starting exactly one.

Any client — the terminal, a local page, a script — asks this module for a service
and gets one, or gets an error that says which of the three things went wrong.
Five decisions are worth writing down, because each one is a refusal of a simpler
design.

- **A file is a rumour; `/health` is the proof.** `read_endpoint` answers whether
  the JSON is well formed and private, not whether anything is behind it. A file
  left by `SIGKILL` keeps its fields, its mode and its owner, so it passes every
  check that is about the file. Discovery therefore adds two facts: the recorded
  pid still exists, and the port answers a short `/health` request with this run's
  token inside a bounded timeout. Anything less would hand a client to a port that
  belongs to something else now.
- **A file is never deleted here.** A stale file may be the endpoint of a run that
  is still publishing, or of one that another client is about to read. Ignoring it
  is enough: the next publication replaces it atomically, and `EndpointPublisher`
  owns the only removal, guarded by its own token.
- **The ownership lock is the only arbiter.** The lock in `ownership.py` is the
  single fact about who may serve a data directory, and the launcher does not
  invent a second one. It asks the OS whether the store is free (`ServerOwnership.free`
  locks and lets go without writing, so the record of who really held it survives),
  and the child that serves takes the lock for real. Two candidates may both see the store free if the first one asks, releases
  and the second asks before the child has taken it; the cost of that window is one
  child that is refused the lock in `cli.run`, before it composes a `Composition`
  and before it binds a port. It is never a second server.
- **The child is a process, not a thread.** The service must outlive the terminal
  that started it, and the terminal is a full-screen interface: a server banner
  printed over it is a broken interface. So the child gets its own session, its own
  output file and no inherited descriptors, and this module watches it instead of
  polling blindly.
- **Every wait has a deadline and every failure has a name.** There is no path here
  that blocks forever, and none that reports a raw `OSError`. A caller either gets
  :class:`Launch` with a live endpoint, or one of the three :class:`LaunchError`
  subclasses below, each of which says what to do next.

What this module deliberately does not do: compose the engine, choose a port, or
decide when the service should stop. The first belongs to `sigil server`, the
second to the configuration it already reads, and the third to the idle-shutdown
slice that comes after this one.
"""

from __future__ import annotations

import http.client
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from src.server.endpoint import Endpoint, read_endpoint
from src.server.ownership import ServerOwnership

HEALTH_PATH = '/health'
"""The one route a probe asks for: unauthenticated, and it answers without state."""

DEFAULT_PROBE_TIMEOUT = 0.5
"""Seconds one liveness request may take. Long enough for a loopback handshake,
short enough that a stranger's silent port costs half a second instead of a hang.
"""

DEFAULT_READY_TIMEOUT = 20.0
"""Seconds to wait for a publication after deciding who launches.

Twenty is about the cost of composing the engine on a slow machine plus starting
uvicorn; it is a deadline, not an expectation, and every test passes its own.
"""

DEFAULT_POLL_INTERVAL = 0.05
"""Seconds between two looks at the endpoint file. Small: the probe is the wait."""

MAX_PROBE_BYTES = 4096
"""Bounded read of the health body. The answer is the status; the body is a gift."""

OUTPUT_TAIL_BYTES = 2048
"""How much of a dead child's output is quoted in the error it caused."""

TERMINATE_TIMEOUT = 5.0
"""Seconds between a polite `terminate` and a `kill` for a child this module owns."""

PROJECT_ROOT = Path(__file__).resolve().parents[2]
"""The checkout this code was imported from: `src/server/launch.py` → repository."""


class Child:
    """A server process this launcher started, and the output it kept.

    The process runs in its own session, so closing the terminal that started it
    does not take the service with it — that is the product decision the lock and
    the idle shutdown, not the terminal, are responsible for. Its standard output
    and error go to an anonymous temporary file: unbounded, because a pipe would
    block the child once it filled; invisible, because a banner drawn over a
    running interface is a broken interface; and readable, because a process that
    dies without publishing should quote what it said.

    Death is noticed with ``poll``: the kernel reports it as soon as it happens and
    the exited child is reaped by that call, so a launcher that watched a child die
    leaves no zombie behind.
    """

    def __init__(self, process: subprocess.Popen[bytes], output: BinaryIO) -> None:
        self._process = process
        self._output = output

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def exited(self) -> bool:
        """Whether the process is gone, reaping it as a side effect."""

        return self._process.poll() is not None

    def exit_code(self) -> int | None:
        """The exit code, or ``None`` while the process is still running."""

        return self._process.poll()

    def wait(self, *, timeout: float | None = None) -> int:
        """Blocks until the process exits, or raises ``subprocess.TimeoutExpired``."""

        return self._process.wait(timeout=timeout)

    def output_tail(self, limit: int = OUTPUT_TAIL_BYTES) -> str:
        """The last ``limit`` bytes the child wrote, decoded leniently.

        Only ever used to explain a failure, so a child that wrote nothing, or a
        file already closed, answers ``''`` instead of raising: the error that is
        being built is the message the caller needs, not this one.
        """

        try:
            self._output.flush()
            size = self._output.seek(0, os.SEEK_END)
            self._output.seek(max(0, size - limit))
            return self._output.read(limit).decode('utf-8', 'replace').strip()
        except OSError, ValueError:
            return ''

    def terminate(self, *, timeout: float = TERMINATE_TIMEOUT) -> None:
        """Stops the child: ``SIGTERM``, then ``SIGKILL`` if it ignores that."""

        if self.alive:
            self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=timeout)

    def close(self) -> None:
        """Releases the output file. The process, if alive, is left running."""

        self._output.close()


class LaunchError(RuntimeError):
    """The service is not reachable and this call did not leave one running."""

    code = 'launch_failed'
    """Stable identifier, for a client that reports the failure without parsing prose."""


class ServiceStarting(LaunchError):
    """Another process owns the store and has not published within the deadline.

    Nothing is wrong: someone else won, and the wait was too short for it. The
    caller retries.
    """

    code = 'service_starting'


class ServiceDied(LaunchError):
    """A process was starting the service and released the store without publishing.

    Its own output, when it produced any, is quoted: a bundle check, a taken port
    and a refused nested invocation all end here, and the reason is in that text.
    """

    code = 'service_died'


class ServiceUnpublished(LaunchError):
    """A process this call started is alive and never published an endpoint.

    The child is still running, so it is carried on the error: the caller decides
    whether to keep waiting or to stop it, and nothing here kills a process that
    might be about to succeed.
    """

    code = 'service_unpublished'

    def __init__(self, message: str, *, child: Child) -> None:
        super().__init__(message)
        self.child = child


@dataclass(frozen=True, slots=True)
class LaunchCommand:
    """How to start a server, as data so a test can aim it at a process double."""

    argv: tuple[str, ...]
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    """Variables *added* to this process's own; ``None`` inherits it unchanged.

    Additions, never a replacement. `SIGIL_SERVICE_DIR` and `SIGIL_SELF_GUARD` are
    inherited facts, and a launcher able to drop them would be a launcher able to
    start a server somewhere other than the one it is looking at.
    """


@dataclass(frozen=True, slots=True)
class Launch:
    """A service a client may talk to now, and whether this call started it.

    ``child`` is the process this call spawned, when it spawned one. It is ``None``
    when the service was already there, which is the common case: the second
    terminal, the second page and every later command attach instead of starting.
    """

    endpoint: Endpoint
    child: Child | None = None

    @property
    def launched(self) -> bool:
        """Whether this call started the process serving this endpoint.

        The answer is an identity check, and it happens where the decision is made:
        `_serving_child` compares the pid the publisher wrote into the endpoint file
        with the pid of the child this call spawned. Equal means this call launched
        the service. Different means another candidate won the lock and this one
        attached to its service, so nothing was launched here — a claim that matters
        to a caller that would otherwise report starting a server it did not start.
        """

        return self.child is not None


def probe(endpoint: Endpoint, *, timeout: float = DEFAULT_PROBE_TIMEOUT) -> bool:
    """Whether something answers ``/health`` on the endpoint's port within ``timeout``.

    `/health` is unauthenticated, so this is not an identity check and cannot become
    one: it says a Sigil-shaped server is listening on that port and answering, not
    that it is *this* run. A port reused by a different service passes here and is
    caught at the first authenticated call, which is the first request that can
    compare a token at all.

    The token still travels, and the answer is read with a bounded body, because the
    request is then shaped like every other request this client makes and a stranger
    cannot answer with an unbounded page. It buys no trust; it saves a surprise.
    """

    connection = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=timeout)
    try:
        connection.request('GET', HEALTH_PATH, headers={'Authorization': f'Bearer {endpoint.token}'})
        response = connection.getresponse()
        response.read(MAX_PROBE_BYTES)
        return response.status == 200
    except OSError, http.client.HTTPException:
        return False
    finally:
        connection.close()


def live_endpoint(path: Path | None = None, *, timeout: float = DEFAULT_PROBE_TIMEOUT) -> Endpoint | None:
    """The endpoint this data directory publishes, if a service is really behind it.

    ``None`` covers every way a file can be a rumour: absent, untrustworthy
    (`read_endpoint`), naming a pid that no longer exists, or naming a port that
    does not answer. None of them is an error — the caller's next step is the same
    for all of them, which is to compete for the lock.
    """

    endpoint = read_endpoint(path)
    if endpoint is None or not _process_alive(endpoint.pid):
        return None
    return endpoint if probe(endpoint, timeout=timeout) else None


def spawn_server(command: LaunchCommand) -> Child:
    """Starts the server as a separate process, and returns the handle to it.

    ``close_fds`` hands the child the three standard streams and nothing else, so a
    lock, a database handle or an event-loop descriptor of this process cannot be
    inherited by accident. ``start_new_session`` puts it in its own session: it is
    not in this terminal's process group, so a `ctrl+c` here is not a shutdown
    there, and no controlling terminal will hang it up.

    Raises:
        OSError: when the command cannot be executed at all.
    """

    # The file outlives this function on purpose: `Child` owns it until the child
    # is gone. A context manager here would close it while the child still writes.
    output = tempfile.TemporaryFile()  # noqa: SIM115
    environment = None if command.env is None else {**os.environ, **command.env}
    try:
        process = subprocess.Popen(
            list(command.argv),
            cwd=None if command.cwd is None else str(command.cwd),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            close_fds=True,
            start_new_session=True,
        )
    except BaseException:
        output.close()
        raise
    return Child(process, output)


def default_command(*, workspace: Path | None = None, entry: Path | None = None) -> LaunchCommand:
    """``python main.py server`` from this checkout: the API, without the web client.

    `server` is the backend surface on purpose. The terminal attaches to the API, and
    it is the one client that does not need a page: launching `serve` instead would
    mount `web/dist` and generate a password that the terminal never shows, printed
    into the anonymous file the child's output goes to. So there is no ``--password``
    and no ``--rebuild-ui`` here — neither flag exists on that subcommand.

    A re-exec of the running interpreter, not the ``sigil`` wrapper: the wrapper
    exists to find the checkout from wherever a human is standing, and this process
    already knows where the code it imported lives. Depending on ``PATH`` instead
    would let the launcher start a server from a different installation than the
    one whose endpoint file it is reading.

    The environment is inherited untouched. If ``SIGIL_SELF_GUARD`` is set, the
    child refuses to start and this launcher reports that as a death carrying the
    child's own message; the guard is a rule about this product, not an obstacle to
    route around.
    """

    root = PROJECT_ROOT if entry is None else Path(entry).resolve().parent
    argv: list[str] = [sys.executable, str(root / 'main.py'), 'server']
    if workspace is not None:
        argv.extend(['--workspace', str(workspace)])
    return LaunchCommand(argv=tuple(argv))


def ensure_service(
    *,
    database: str | Path,
    command: LaunchCommand | None = None,
    endpoint_path: Path | None = None,
    wait: float = DEFAULT_READY_TIMEOUT,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT,
    interval: float = DEFAULT_POLL_INTERVAL,
) -> Launch:
    """A live service to talk to, starting one only when there is none.

    The algorithm, in order:

    1. Read the endpoint and prove it alive (``live_endpoint``). A live one is
       returned at once: attaching is the common case and costs one request.
    2. Otherwise ignore the file, without deleting it, and ask the ownership lock
       whether the store is free right now.
    3. Free: this call is the candidate. Start the server (`spawn_server`) — the
       child takes the lock for real, and a losing child exits before it binds a
       port, so there is never a second server.
    4. Not free: another process is starting one. Wait for its publication.
    5. Either way, look at the endpoint file and the child every ``interval`` until
       ``wait`` seconds have passed, then report one of the three failures below.

    Args:
        database: The database whose lock arbitrates, normally `database_path()`.
            The child must resolve the same path, which is why the environment is
            inherited rather than rebuilt.
        command: How to start a server. Defaults to `default_command`; a test, or a
            packaging that ships a console script, passes its own.
        endpoint_path: The rendezvous file. Defaults to the one this data directory
            publishes, so the normal call passes nothing here either.
        wait: Seconds allowed for a publication after the decision. This deadline is
            the reason no caller of this function can hang.
        probe_timeout: Seconds one liveness request may take.
        interval: Seconds between two observations while waiting.

    Returns:
        The endpoint of a live service, and the child when this call started it.

    Raises:
        ServiceStarting: another process owns the store and did not publish within
            ``wait``. The caller retries.
        ServiceDied: the process that was starting the service released the store
            without publishing. Its output is quoted when it said anything.
        ServiceUnpublished: a process this call started is alive and did not publish
            within ``wait``. The child travels on the error.
        OwnershipError: the lock path is unsafe, which is a configuration failure
            and not a race, so it is never waited on.
    """

    target = Path(database)
    endpoint = live_endpoint(endpoint_path, timeout=probe_timeout)
    if endpoint is not None:
        return Launch(endpoint=endpoint)
    waiting_for_other = not _store_free(target)
    child = None if waiting_for_other else spawn_server(command if command is not None else default_command())
    return _await_publication(
        child=child,
        waiting_for_other=waiting_for_other,
        database=target,
        endpoint_path=endpoint_path,
        wait=wait,
        probe_timeout=probe_timeout,
        interval=interval,
    )


def _await_publication(
    *,
    child: Child | None,
    waiting_for_other: bool,
    database: Path,
    endpoint_path: Path | None,
    wait: float,
    probe_timeout: float,
    interval: float,
) -> Launch:
    """Watches for a publication until one appears or the deadline explains its absence."""

    deadline = time.monotonic() + wait
    started = child
    while True:
        endpoint = live_endpoint(endpoint_path, timeout=probe_timeout)
        if endpoint is not None:
            return Launch(endpoint=endpoint, child=_serving_child(started, endpoint))
        if started is not None and started.exited():
            exited, started = started, None
            # The child is gone. Who owns the store decides what that means: free
            # means it died on the way in; held means a competing candidate won and
            # this call is now a loser, which is still a chance to attach.
            if _store_free(database):
                raise ServiceDied(_death_message(exited))
            waiting_for_other = True
        elif waiting_for_other and _store_free(database):
            # The store is free, so whoever held it let go — which is not yet proof
            # that nobody is coming back. Another launcher holds the lock for the
            # length of one probe, releases, and only then spawns the child that
            # takes it for real, so a free store can mean "a candidate is between two
            # steps". The record the lock writes names the last holder, and only that
            # process being gone means the publication we are waiting for cannot come.
            pid = _owner_pid(database)
            if pid is not None and not _process_alive(pid):
                raise ServiceDied(
                    f'The process starting the Sigil service (pid {pid}) is gone without publishing an endpoint.'
                )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _deadline_error(started=started, database=database, wait=wait)
        time.sleep(min(interval, remaining))


def _serving_child(started: Child | None, endpoint: Endpoint) -> Child | None:
    """The child this call started, when the live endpoint is really served by it.

    A candidate that spawned a child, lost the lock to another candidate and then
    attached to the winner's service must not report that it launched anything, so
    the identity check is made here, at the decision: the pid the publisher wrote
    into the endpoint file is compared with the pid of the child this call spawned,
    and only ``pid == endpoint.pid`` returns the child. A mismatch is reaped on the
    way out, so a process that waited for somebody else does not keep a zombie for
    the rest of its life, and `Launch.launched` then answers ``False`` because the
    child it holds is ``None``.
    """

    if started is None:
        return None
    if started.pid == endpoint.pid:
        return started
    started.exited()
    return None


def _death_message(child: Child) -> str:
    """The failure, with the child's own words when it had any."""

    code = child.exit_code()
    tail = child.output_tail()
    message = f'The Sigil server exited before publishing an endpoint (exit code {code})'
    return f'{message}:\n{tail}' if tail else f'{message}.'


def _deadline_error(*, started: Child | None, database: Path, wait: float) -> LaunchError:
    """The typed failure that matches what was observed at the deadline."""

    if started is not None:
        return ServiceUnpublished(
            f'The Sigil server (pid {started.pid}) is running but published no endpoint within {wait:.1f}s.',
            child=started,
        )
    lock = _lock_path(database)
    if _store_free(database):
        return ServiceDied(
            f'No Sigil service answered: {lock} is free and nothing published an endpoint within {wait:.1f}s.'
        )
    return ServiceStarting(
        f'Another process owns {lock} and published no endpoint within {wait:.1f}s. '
        'It is still starting; run the same command again in a moment.'
    )


def _store_free(database: Path) -> bool:
    """Whether the store is free, re-raising a lock path no wait can repair.

    The question is asked with `ServerOwnership.free`, which locks and lets go
    without writing: the ``pid=`` record has to survive so this loop can still tell
    a dead owner from an owner that is between two steps.
    """

    return ServerOwnership(database).free()


def _lock_path(database: Path) -> Path:
    """Where the arbitration happens, derived by the module that owns the lock."""

    return ServerOwnership(database).path


def _owner_pid(database: Path) -> int | None:
    """The pid the last holder of the lock recorded, or ``None`` when unknown."""

    return ServerOwnership(database).owner_pid()


def _process_alive(pid: int) -> bool:
    """Whether the process that published the file can still act.

    A pid the kernel says is gone is the easy half. The other half is a process that
    exited and has not been reaped yet: it still has a pid, but it cannot serve or
    publish anything, so for this module it is gone too. Only a platform without
    ``/proc`` falls back to "present means alive".

    A live pid proves nothing — pids are reused — so this answers only the cheap
    half of the question: a pid that cannot act is a file whose owner cannot be the
    service. Everything else is left to the health probe.
    """

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return not _is_zombie(pid)


def _is_zombie(pid: int) -> bool:
    """Whether an existing pid is a process that already exited, waiting to be reaped."""

    try:
        stat = Path(f'/proc/{pid}/stat').read_text(encoding='ascii', errors='replace')
    except OSError:
        return False
    # `pid (comm) state ...`, and `comm` may contain spaces and parentheses, so the
    # state is the first field after the last ')'.
    fields = stat.rpartition(')')[2].split()
    return bool(fields) and fields[0] == 'Z'


__all__ = [
    'DEFAULT_POLL_INTERVAL',
    'DEFAULT_PROBE_TIMEOUT',
    'DEFAULT_READY_TIMEOUT',
    'HEALTH_PATH',
    'Child',
    'Launch',
    'LaunchCommand',
    'LaunchError',
    'ServiceDied',
    'ServiceStarting',
    'ServiceUnpublished',
    'default_command',
    'ensure_service',
    'live_endpoint',
    'probe',
    'spawn_server',
]
