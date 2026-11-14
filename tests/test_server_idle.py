"""When the service has work, and what "no work" does to the process.

Two levels prove this cut, and the level is chosen per behaviour:

- **Facts about the database and about the turn**, tested against a real queue in a
  temporary directory and a real turn driven by a model that waits: a running turn,
  a row accepted by *another* session, a row left by a crash, a row a graceful stop
  must not touch. No socket is involved because none is needed.
- **The exit itself**, tested with a real child process: uvicorn leaving its loop,
  the endpoint file disappearing and the ownership lock becoming acquirable are
  facts about a process. A mock would prove nothing about them.

The grace window is advanced, never slept through: the clock is an argument
everywhere the decision reads it, so a thirty-minute window costs nothing.

`TestModel` stands in for a network model for the same reason
`test_server_runtime.py` uses it: the events a real model produces are a subset
of what this exercises, and no test may depend on a provider.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from pydantic_ai.models.test import TestModel

from src.engine.bootstrap import Composition
from src.engine.settings import load_settings
from src.engine.storage.accepted_inputs import AcceptedInputs, DeliveryConfirmation, confirm_delivery
from src.engine.storage.database import database_path
from src.server.app import GracefulStop
from src.server.cli import build_parser
from src.server.endpoint import read_endpoint
from src.server.events import EventHub
from src.server.launch import live_endpoint
from src.server.runtime import (
    DEFAULT_IDLE_GRACE,
    IDLE_POLL_INTERVAL,
    IdleShutdown,
    PendingQuestion,
    ServerRuntime,
)

ROOT = Path(__file__).resolve().parents[1]
"""The checkout, so a child process can import `src.*` without `PYTHONPATH`."""

LOOK_INTERVAL = 0.05
"""Seconds between two observations while waiting for something observable."""

PUBLISHED_DEADLINE = 60.0
"""Seconds allowed for a real child to compose the engine and publish."""

EXIT_DEADLINE = 60.0
"""Seconds allowed for the child to leave its loop once it published."""


def _paths(tmp_path: Path):
    from src.engine.providers.bootstrap import ProviderPaths

    home = tmp_path / '.sigil'
    home.mkdir(exist_ok=True)
    return ProviderPaths(home)


def _composition(tmp_path: Path, model: object | None = None) -> Composition:
    """A real composition with a stub model and its storage in a temporary directory."""
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        model=model or TestModel(custom_output_text='done', call_tools=[]),
        provider_paths=_paths(tmp_path),
    )
    composition.meter.set_window(100_000)
    return composition


@pytest.fixture
def composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Composition:
    """A composition whose database belongs to this test alone.

    The queue question is database-wide by design, so a row another test left
    behind would be indistinguishable from work this one created. Pointing
    `SIGIL_HOME` at the test's own directory gives the store — and the ownership
    lock next to it — a database nobody else writes to.
    """
    monkeypatch.setenv('SIGIL_HOME', str(tmp_path / 'home'))
    return _composition(tmp_path)


@pytest.fixture
def runtime(composition: Composition) -> ServerRuntime:
    return ServerRuntime(composition, EventHub(), require_ownership=False)


class Clock:
    """A monotonic clock a test advances by hand: the window without the wait."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def _accept(composition: Composition, text: str = 'pendiente') -> str:
    """Accepts one input for a session that is *not* the one the runtime serves."""
    session = composition.store.draft_session()
    request = str(uuid.uuid4())
    composition.store.accept_input(session, request, text, [], 'turn')
    return session.id


def _state(composition: Composition, session_id: str) -> str:
    return AcceptedInputs(composition.store.database).list(session_id)[0].state


# --- the decision, with a clock instead of a wait ---------------------------


def test_the_window_expires_only_after_it_has_elapsed() -> None:
    """The grace window is the whole difference between "idle" and "stop"."""

    clock = Clock()
    idle = IdleShutdown(lambda: False, grace=30.0, clock=clock)

    assert idle.expired() is False  # the first look starts the window
    clock.advance(29.9)
    assert idle.expired() is False
    clock.advance(0.1)
    assert idle.expired() is True


def test_work_restarts_the_whole_window() -> None:
    """A burst of short turns must not add up to a shutdown."""

    clock = Clock()
    working = False
    idle = IdleShutdown(lambda: working, grace=30.0, clock=clock)

    assert idle.expired() is False  # the window starts here
    clock.advance(29.0)
    working = True
    assert idle.expired() is False  # work seen: the window is dropped
    working = False
    assert idle.expired() is False  # and restarts from now
    clock.advance(29.0)
    assert idle.expired() is False  # 29 seconds into the *new* window
    clock.advance(1.0)
    assert idle.expired() is True


def test_a_work_check_that_fails_never_stops_the_service(
    runtime: ServerRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A database that cannot answer is not proof that nothing is left to do."""

    def broken() -> bool:
        raise RuntimeError('database is gone')

    monkeypatch.setattr(runtime, 'has_active_work', broken)

    async def go() -> tuple[list[str], list[str]]:
        asked: list[str] = []
        watch = asyncio.create_task(
            runtime.stop_when_idle(lambda: asked.append('stop'), grace=0.0, poll=0.0, clock=Clock())
        )
        try:
            for _ in range(20):
                await asyncio.sleep(0)  # a turn of the loop, not a wait
            assert asked == []
        finally:
            watch.cancel()
            try:
                await watch
            except asyncio.CancelledError:
                pass
        notices = [event.data['text'] for event in runtime.hub.replay() if event.type == 'notice']
        return asked, notices

    asked, notices = asyncio.run(go())

    assert asked == []
    # One notice per streak: a failing check must not turn the log into the alarm.
    assert len([notice for notice in notices if notice.startswith('Idle check')]) == 1


# --- what counts as work ---------------------------------------------------


def test_every_non_terminal_state_counts_as_open(composition: Composition) -> None:
    """`has_open` is the database-wide question, and it names all three states."""

    accepted = AcceptedInputs(composition.store.database)
    session = composition.store.draft_session()
    request = str(uuid.uuid4())
    composition.store.accept_input(session, request, 'hola', [], 'turn')
    assert accepted.has_open() is True

    claimed = accepted.claim_next(session.id, attempt_id='attempt')
    assert claimed is not None and claimed.state == 'claimed'
    assert accepted.has_open() is True

    with composition.store.database.transaction() as conn:
        confirm_delivery(
            conn,
            [DeliveryConfirmation(session.id, request, 'attempt', 'run')],
            destination_id=session.id,
        )
    assert accepted.has_open() is True  # delivered, and still not finished

    accepted.transition(session.id, request, 'finished')
    assert accepted.has_open() is False


def test_a_terminal_row_is_not_work(composition: Composition) -> None:
    accepted = AcceptedInputs(composition.store.database)
    session = composition.store.draft_session()
    request = str(uuid.uuid4())
    composition.store.accept_input(session, request, 'hola', [], 'turn')
    accepted.transition(session.id, request, 'stopped')

    assert accepted.has_open() is False


def test_a_row_accepted_by_another_session_keeps_the_service_alive(runtime: ServerRuntime) -> None:
    """The service serves one database, not one session: another client's work is work."""

    session_id = _accept(runtime.composition)
    assert session_id != runtime.session.id
    assert runtime.has_active_work() is True

    clock = Clock()
    idle = IdleShutdown(runtime.has_active_work, grace=30.0, clock=clock)
    clock.advance(3_600.0)  # an hour past the window, without the hour
    assert idle.expired() is False


async def _await_question(runtime: ServerRuntime, deadline: float) -> PendingQuestion:
    """Waits until a blocked turn publishes its question, or the deadline expires.

    Event-driven: the subscription is the wait, and `LOOK_INTERVAL` only bounds how
    often the queue is looked at, so nothing here sleeps a guessed duration.
    """
    expires = time.monotonic() + deadline
    async with runtime.hub.subscribe() as subscription:
        while time.monotonic() < expires:
            event = await subscription.next(timeout=LOOK_INTERVAL)
            pending = runtime.pending_question
            if event is not None and event.type == 'question' and pending is not None:
                return pending
    raise AssertionError('the turn never asked its question')


def test_a_running_turn_keeps_the_service_alive(tmp_path: Path) -> None:
    """A turn parked on a question is still a turn: nothing may stop underneath it.

    `TestModel` is told to call `ask_user`, so the turn waits for an answer only
    this test gives. That is also the plan's rule: there are no interactive
    approvals, and a pending question is covered by the running turn rather than by
    a rule of its own.
    """

    async def go() -> None:
        composition = _composition(tmp_path, TestModel(custom_output_text='done', call_tools=['ask_user']))
        runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
        try:
            await runtime.send('hola')
            pending = await _await_question(runtime, EXIT_DEADLINE)

            assert runtime.busy is True
            assert runtime.has_active_work() is True

            clock = Clock()
            idle = IdleShutdown(runtime.has_active_work, grace=30.0, clock=clock)
            clock.advance(3_600.0)  # an hour past the window, without the hour
            assert idle.expired() is False

            assert runtime.answer(pending.id, [{'question': pending.questions[0].question, 'answer': 'si'}])
            await asyncio.wait_for(runtime.wait(), timeout=EXIT_DEADLINE)

            assert runtime.busy is False
            assert runtime.has_active_work() is False
            assert idle.expired() is False  # the window starts once work is gone
            clock.advance(30.0)
            assert idle.expired() is True
        finally:
            runtime.close()

    asyncio.run(go())


def test_a_connected_event_subscriber_is_not_work(runtime: ServerRuntime) -> None:
    """A stream can reconnect to whatever service is there, so it is not work.

    The plan decides this explicitly: a browser tab left open must not keep a
    process alive, or an idle service would never stop for anyone with a tab.
    """

    async def go() -> None:
        subscription = runtime.hub.subscribe()
        try:
            assert runtime.hub.subscriber_count == 1
            assert runtime.has_active_work() is False
        finally:
            subscription.close()

    asyncio.run(go())


# --- the stop itself, and what it must not do ------------------------------


def test_the_watch_asks_for_a_stop_once_the_window_passes(runtime: ServerRuntime) -> None:
    """The loop asks exactly once, through the callable it was given."""

    async def go() -> list[str]:
        asked: list[str] = []
        await asyncio.wait_for(
            runtime.stop_when_idle(lambda: asked.append('stop'), grace=0.0, poll=0.0, clock=Clock()),
            timeout=EXIT_DEADLINE,
        )
        return asked

    assert asyncio.run(go()) == ['stop']


def test_the_watch_never_asks_while_work_exists(runtime: ServerRuntime) -> None:
    """A row from another session keeps the service up long past the window.

    The clock jumps ten seconds between looks, so after four of them the window is
    gone: a decision that ignored `has_open` — or that only saw the session this
    runtime serves — would already have asked for a stop.
    """

    class Jumping(Clock):
        def __call__(self) -> float:
            self.now += 10.0
            return self.now

    async def go() -> list[str]:
        _accept(runtime.composition)  # another session's row, not the served one
        asked: list[str] = []
        watch = asyncio.create_task(
            runtime.stop_when_idle(lambda: asked.append('stop'), grace=30.0, poll=0.0, clock=Jumping())
        )
        try:
            for _ in range(50):
                await asyncio.sleep(0)  # a turn of the loop, not a wait
            assert asked == []
        finally:
            watch.cancel()
            try:
                await watch
            except asyncio.CancelledError:
                pass
        return asked

    assert asyncio.run(go()) == []


def test_a_short_window_is_not_overshot_by_a_long_poll(runtime: ServerRuntime) -> None:
    """The poll is a ceiling, not the resolution: a 50 ms window is not seen at 5 s.

    `--idle-grace` is a promise about how long an idle service lives, so the loop may
    not sleep longer than the window it is watching.
    """

    async def go() -> list[str]:
        asked: list[str] = []
        await asyncio.wait_for(
            runtime.stop_when_idle(lambda: asked.append('stop'), grace=0.05, poll=5.0),
            timeout=2.0,
        )
        return asked

    assert asyncio.run(go()) == ['stop']


def test_a_graceful_close_leaves_the_rows_where_they_were(composition: Composition) -> None:
    """Orderly shutdown is not a crash: only reconciliation may interrupt rows.

    A row can land between the last idle look and the loop leaving. Marking it
    interrupted would claim it was lost to a crash when nothing crashed.
    """

    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
    runtime.start()
    session_id = _accept(composition)

    runtime.close()

    assert _state(composition, session_id) == 'accepted'


def test_a_row_left_by_a_crash_is_still_interrupted_on_the_next_start(composition: Composition) -> None:
    """Crash reconciliation is untouched: the next start interrupts what it finds."""

    session_id = _accept(composition, 'left by a crash')
    runtime = ServerRuntime(composition, EventHub(), require_ownership=False)
    try:
        runtime.start()
        assert _state(composition, session_id) == 'interrupted'
    finally:
        runtime.close()


# --- the configuration -----------------------------------------------------


def test_the_grace_window_is_configurable_from_the_command_line() -> None:
    parser = build_parser()

    assert parser.parse_args(['--idle-grace', '0.25']).idle_grace == 0.25
    assert parser.parse_args([]).idle_grace == DEFAULT_IDLE_GRACE
    assert IDLE_POLL_INTERVAL > 0


def test_a_zero_window_is_allowed_and_means_no_wait() -> None:
    """Zero is a legitimate window: stop as soon as the last work is gone."""

    assert build_parser().parse_args(['--idle-grace', '0']).idle_grace == 0.0


@pytest.mark.parametrize('value', ['-1', '-0.5', 'nan', 'inf', '-inf', '1e30', 'forever'])
def test_a_value_that_is_not_a_window_is_refused(value: str) -> None:
    """A negative, unmeasurable or absurd number is not a duration, and says so.

    `nan` would make every comparison false and keep a service alive for ever; `inf`
    and a day-long value are the same promise spelled differently; argparse turns this
    into exit code 2 with the reason, which is the whole point of rejecting it here.
    """

    with pytest.raises(SystemExit):
        build_parser().parse_args(['--idle-grace', value])


def test_a_stop_requested_before_the_loop_exists_is_not_lost() -> None:
    """The app is built before uvicorn, so a request can precede the binding."""

    stop = GracefulStop()
    assert stop.requested is False

    stop.request()  # nothing to bind yet
    assert stop.requested is True

    bound: list[str] = []
    stop.bind(lambda: bound.append('stop'))
    assert bound == ['stop']


def test_a_bound_stop_is_requested_after_the_binding_too() -> None:
    stop = GracefulStop()
    bound: list[str] = []
    stop.bind(lambda: bound.append('stop'))
    assert bound == []

    stop.request()

    assert bound == ['stop']
    assert stop.requested is True


# --- the exit, with a real process -----------------------------------------

CHILD = """
import sys
from pathlib import Path

sys.path.insert(0, {root!r})

import uvicorn
from pydantic_ai.models.test import TestModel

from src.engine.bootstrap import Composition
from src.engine.settings import load_settings
from src.server.app import GracefulStop, create_app
from src.server.cli import listening_socket
from src.server.config import ServerConfig
from src.server.endpoint import EndpointPublisher
from src.server.events import EventHub
from src.server.ownership import ServerOwnership
from src.server.runtime import ServerRuntime

composition = Composition(
    {workspace!r},
    settings=load_settings(),
    model=TestModel(custom_output_text='done', call_tools=[]),
)
ownership = ServerOwnership(composition.store.database.path)
ownership.acquire()
runtime = ServerRuntime(composition, EventHub(), ownership=ownership)

sock = listening_socket('127.0.0.1', 0)
config = ServerConfig(host='127.0.0.1', port=sock.getsockname()[1], password='test')
stop = GracefulStop()
app = create_app(
    runtime,
    config,
    dist=Path({dist!r}),
    publisher=EndpointPublisher({endpoint!r}),
    request_shutdown=stop.request,
    idle_grace={grace!r},
    idle_poll={poll!r},
)
server = uvicorn.Server(
    uvicorn.Config(app, host=config.host, port=config.port, log_level='warning', access_log=False)
)
stop.bind(lambda: setattr(server, 'should_exit', True))
server.run(sockets=[sock])
print('LEFT THE LOOP', flush=True)
sock.close()
"""

ACQUIRE = """
import sys

sys.path.insert(0, {root!r})

from src.server.ownership import OwnershipError, ServerOwnership

try:
    ServerOwnership({database!r}).acquire()
except OwnershipError:
    print('REFUSED')
else:
    print('ACQUIRED')
"""


def _wait_for_publication(endpoint: Path, process: subprocess.Popen[str]) -> None:
    """Waits until the child answers on the port its endpoint file names."""

    deadline = time.monotonic() + PUBLISHED_DEADLINE
    while live_endpoint(endpoint, timeout=0.5) is None:
        assert process.poll() is None, process.stdout.read() if process.stdout else ''
        assert time.monotonic() < deadline, 'the child never published an endpoint'
        time.sleep(LOOK_INTERVAL)


def _may_serve(database: Path) -> str:
    """Asks a real child process whether it may take the lock, and reports its answer."""

    result = subprocess.run(
        [sys.executable, '-c', ACQUIRE.format(root=str(ROOT), database=str(database))],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=EXIT_DEADLINE,
        check=False,
    )
    return result.stdout.strip()


def test_a_service_with_no_work_stops_after_the_window_and_releases_the_store(tmp_path: Path) -> None:
    """The whole exit: the loop ends by itself, the file goes, the lock is free.

    This is the one behaviour of the cut that is a fact about a process, so it is
    the one proven with a process: a real uvicorn serving a real runtime, with a
    grace window short enough to observe and a poll interval short enough to see.
    """

    home = tmp_path / 'home'
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    endpoint = tmp_path / 'service' / 'endpoint.json'
    script = tmp_path / 'idle_service.py'
    script.write_text(
        CHILD.format(
            root=str(ROOT),
            workspace=str(workspace),
            dist=str(tmp_path / 'missing-dist'),
            endpoint=str(endpoint),
            grace=0.1,
            poll=0.02,
        ),
        encoding='utf-8',
    )
    environment = {**os.environ, 'SIGIL_HOME': str(home), 'SIGIL_SERVICE_DIR': str(endpoint.parent)}
    process = subprocess.Popen(
        [sys.executable, str(script)],
        cwd=str(ROOT),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_publication(endpoint, process)
        output, _ = process.communicate(timeout=EXIT_DEADLINE)
    finally:
        if process.poll() is None:  # pragma: no cover - only on a failure above
            process.kill()
            process.wait(timeout=EXIT_DEADLINE)

    assert process.returncode == 0, output
    assert 'LEFT THE LOOP' in output
    assert read_endpoint(endpoint) is None, 'the endpoint of a stopped service must not survive it'
    assert _may_serve(database_path(home)) == 'ACQUIRED', 'the service kept the store after leaving'


def test_background_agents_keep_service_alive(runtime: ServerRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    running = 1
    monkeypatch.setattr(runtime.composition.summoner, 'running', lambda: running)
    clock = Clock()
    idle = IdleShutdown(runtime.has_active_work, grace=30, clock=clock)
    assert not runtime.busy
    assert not runtime._accepted.has_open()
    assert runtime.has_active_work()
    assert not idle.expired()
    clock.advance(1800)
    assert not idle.expired(), 'An unfinished background agent is not an idle service'
    running = 0
    assert not runtime.has_active_work()
    assert not idle.expired(), 'The grace window starts only after the worker finishes'
    clock.advance(30)
    assert idle.expired()
