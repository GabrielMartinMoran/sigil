"""`sigil server` and `sigil serve`: the terminal-facing entry point of the web server.

Two surfaces, one process:

- **`sigil server`** is the API alone. No bundle is mounted, no login route exists and
  no password is invented — local clients authenticate with the token the endpoint
  file publishes. A browser that opens `/` is told which command serves the page.
- **`sigil serve`** is the same API plus the web client: `web/dist` is mounted, `/`
  and `/static` are served, and the password gate is in front of `/api/*`.

What this file owns:

- **Arguments.** `--host` and `--port` for the bind, `--workspace` and `--idle-grace`
  for the engine; `--password` and `--rebuild-ui` exist only on `sigil serve`, because
  they are things only the web client needs.
- **The order of the checks.** On `serve`, the bundle is verified before the socket
  opens: a server that starts and then serves a 503 is worse than one that refuses to
  start and says which command fixes it. `server` makes no such check.
- **The socket, before uvicorn.** `listening_socket` binds and listens here, and
  uvicorn is handed the finished socket. The reason is the endpoint file: uvicorn
  starts the application's lifespan *before* it creates a listening socket of its
  own, so publishing from the lifespan would name a port that was not open yet. A
  socket this file already bound makes the publication honest — the kernel accepts
  the connection while uvicorn is still starting.
- **The banner.** Printed before `uvicorn` takes over, because a generated password
  exists only here and the log line that follows is not the place to look for it.

`--password` is deliberately not stored anywhere: it is the flag for "I am testing
this right now", and the environment variable is the one that survives a restart.
"""

from __future__ import annotations

import argparse
import math
import socket
import sys
from types import FrameType

from dotenv import load_dotenv

from src.engine.bootstrap import Composition
from src.server import webbuild
from src.server.app import GracefulStop, create_app
from src.server.config import PASSWORD_ENV, ServerConfig, ServerConfigError, banner, resolve_config
from src.server.diagnostics import diagnose_server, record
from src.server.endpoint import EndpointPublisher
from src.server.events import EventHub
from src.server.ownership import OwnershipError, ServerOwnership
from src.server.runtime import DEFAULT_IDLE_GRACE, ServerRuntime

MAX_IDLE_GRACE = 24 * 60 * 60.0
"""Largest accepted ``--idle-grace``: one day.

Beyond that the number is not a bound on a process's life, it is a way of spelling
"never stop" in a flag that exists to bound it, so it is refused instead of silently
authorising an immortal service.
"""

DEFAULT_BACKLOG = 2048
"""Pending connections the kernel may queue before uvicorn starts accepting them.

The same number uvicorn uses by default; the point is that the queue exists from
the moment this file calls ``listen``, which is before any publication.
"""


def listening_socket(host: str, port: int) -> socket.socket:
    """A socket already bound and listening, ready to be handed to uvicorn.

    Raises:
        OSError: when the address cannot be bound, so the caller can report the
            port as taken instead of starting and failing later.
    """

    family, kind, protocol, _, address = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
    sock = socket.socket(family, kind, protocol)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(address)
        sock.listen(DEFAULT_BACKLOG)
    except OSError:
        sock.close()
        raise
    return sock


def idle_grace(value: str) -> float:
    """Parses the window, refusing values that are not a duration.

    Zero is allowed and means what it says: stop on the first look that finds no
    work, with the poll still bounding how often that look happens. A negative
    number, ``nan`` or ``inf`` is not a window — ``nan`` in particular would make
    every comparison false and keep the service alive for ever — and a value over a
    day is refused for the reason in `MAX_IDLE_GRACE`.

    Raises:
        argparse.ArgumentTypeError: so the flag exits 2 with the reason, instead of
            a negative window silently meaning "never" or ``nan`` meaning "always".
    """
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f'not a number of seconds: {value!r}') from exc
    if not math.isfinite(seconds):
        raise argparse.ArgumentTypeError(f'must be a finite number of seconds: {value!r}')
    if seconds < 0:
        raise argparse.ArgumentTypeError(f'must not be negative: {value!r}')
    if seconds > MAX_IDLE_GRACE:
        raise argparse.ArgumentTypeError(f'must be at most {MAX_IDLE_GRACE:g} seconds: {value!r}')
    return seconds


def build_parser(web: bool = True) -> argparse.ArgumentParser:
    """The flags of one surface. `serve` gets the two the web client needs.

    ``--password`` and ``--rebuild-ui`` are absent, not ignored, on the backend: a
    flag that exists but does nothing is a promise the process does not keep, and a
    password flag on an API with no login form is exactly that.
    """
    serve = 'serve' if web else 'server'
    parser = argparse.ArgumentParser(
        prog=f'sigil {serve}',
        description=(
            'Serves the Sigil API and the web client, with a password gate.'
            if web
            else 'Serves the Sigil API for local clients, without the web client.'
        ),
    )
    parser.add_argument('--host', help='address to bind (default 127.0.0.1; 0.0.0.0 for the LAN)')
    parser.add_argument('--port', type=int, help='port to bind (default 8760)')
    if web:
        parser.add_argument(
            '--password',
            help=f'password for this run, not stored (the {PASSWORD_ENV} variable is the persistent one)',
        )
        parser.add_argument(
            '--rebuild-ui',
            action='store_true',
            help='rebuild web/dist from web/ before serving (needs Node)',
        )
    parser.add_argument('--workspace', help='directory to open, instead of the current one')
    parser.add_argument(
        '--idle-grace',
        type=idle_grace,
        default=DEFAULT_IDLE_GRACE,
        metavar='SECONDS',
        help=(
            'seconds with no work before the service stops itself; 0 is valid and means '
            f'stop on the first idle look (default {DEFAULT_IDLE_GRACE:g})'
        ),
    )
    parser.add_argument('--reload', action='store_true', help=argparse.SUPPRESS)
    return parser


def run(argv: list[str] | None = None, *, web: bool = True) -> int:
    """Run the server with persistent lifecycle and failure diagnostics."""
    args = build_parser(web).parse_args(argv)
    # Configure paths before opening diagnostic files; preserve process overrides.
    load_dotenv()
    with diagnose_server():
        code = _run(args, web=web)
        record('server_command_finished', exit_code=code)
        return code


def _run(args: argparse.Namespace, *, web: bool = True) -> int:
    """Starts the server. Returns the process exit code.

    ``web`` selects the surface: ``False`` is the API alone (`sigil server`), ``True``
    adds the bundle, the login route and the password (`sigil serve`).
    """
    program = 'sigil serve' if web else 'sigil server'

    try:
        config = resolve_config(host=args.host, port=args.port, password=args.password, web=web)
    except ServerConfigError as exc:
        print(f'{program}: {exc}', file=sys.stderr)
        return 2

    if web and args.rebuild_ui:
        try:
            built = webbuild.build()
        except webbuild.BuildError as exc:
            print(f'{program}: {exc}', file=sys.stderr)
            return 2
        print(f'{program}: web client rebuilt ({built})')

    if web:
        state = webbuild.staleness()
        if state.stale:
            print(f'{program}: {state.message()}', file=sys.stderr)
            return 2

    # Imported here: uvicorn pulls in a lot, and `--help` should not pay for it.
    import uvicorn

    class LoggedServer(uvicorn.Server):
        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            record('server_signal_received', signal=sig)
            super().handle_exit(sig, frame)

    from src.engine.settings import load_settings
    from src.engine.storage.database import database_path

    # The lock is taken before the engine is built, and it is held until the process
    # closes. `Composition` reads settings, the MCP config and the skill registry, so
    # paying for it only to be refused at `start` is the wrong order of the bill;
    # taking it here also means a second candidate is refused *while* this one
    # composes, not after. The runtime adopts this object, so the lock is opened once
    # for the whole life of the process — `flock` counts two descriptors of one
    # process as two holders, and a second acquisition would refuse ourselves.
    ownership = ServerOwnership(database_path())
    try:
        ownership.acquire()
    except OwnershipError as exc:
        print(f'{program}: {exc}', file=sys.stderr)
        return 2

    try:
        settings = load_settings()
        composition = Composition(root=args.workspace, settings=settings)
        hub = EventHub()
        runtime = ServerRuntime(composition, hub, ownership=ownership)
    except BaseException:
        # Nothing adopted the lock: the engine never got built, so it is still ours
        # to release before this failure leaves the caller.
        ownership.release()
        raise

    try:
        runtime.start()
        publisher = EndpointPublisher()
        # The app is built before the server that runs it, and the server is what can
        # stop: `GracefulStop` is the one-way request the idle watch makes, bound to
        # uvicorn's own switch as soon as that object exists. Nothing here ends the
        # process directly — the loop leaves, the lifespan shutdown unpublishes the
        # endpoint, closes the runtime and releases the ownership lock.
        stop = GracefulStop()
        app = create_app(
            runtime,
            config,
            publisher=publisher,
            request_shutdown=stop.request,
            idle_grace=args.idle_grace,
        )

        try:
            sock = listening_socket(config.host, config.port)
        except OSError as exc:
            print(f'{program}: cannot listen on {config.host}:{config.port}: {exc}', file=sys.stderr)
            return 2

        print(banner(config), flush=True)
        print(flush=True)

        server = LoggedServer(
            uvicorn.Config(
                app,
                host=config.host,
                port=config.port,
                log_level='warning',
                access_log=False,
                # A browser tab and an event stream are two long-lived connections; the
                # default timeout would cut the stream while a long tool runs.
                timeout_keep_alive=75,
            )
        )
        # uvicorn's own graceful stop, and the same flag its signal handlers set: the
        # loop finishes what it is serving and then runs the lifespan shutdown. The
        # flag is a plain attribute the server reads on each turn of its loop, so it
        # is set with `setattr` rather than by calling `server.shutdown()`, which is
        # private and would skip the orderly unwinding this whole path depends on.
        stop.bind(lambda: setattr(server, 'should_exit', True))
        record('server_listening', port=int(sock.getsockname()[1]), idle_grace=args.idle_grace)
        try:
            server.run(sockets=[sock])
        except KeyboardInterrupt:  # pragma: no cover - the normal way to stop it
            pass
        finally:
            sock.close()
        return 0
    finally:
        # One close for every exit above, including a failed `start`: the runtime owns
        # the lock from the moment it adopted it, and closing releases it exactly once.
        runtime.close()


def describe(config: ServerConfig) -> str:
    """The banner, exposed for tests that assert on what a user is told."""
    return banner(config)


__all__ = ['build_parser', 'describe', 'listening_socket', 'run']
