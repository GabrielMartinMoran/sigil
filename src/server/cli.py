"""`sigil server`: the terminal-facing entry point of the web server.

What this file owns:

- **Arguments.** `--host` and `--port` for the bind, `--password` for a throwaway
  secret, `--rebuild-ui` for the one case where Node is available and wanted.
- **The order of the checks.** The bundle is verified before the socket opens: a
  server that starts and then serves a 503 is worse than one that refuses to start
  and says which command fixes it.
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
import socket
import sys

from dotenv import load_dotenv

from src.engine.bootstrap import Composition
from src.server import webbuild
from src.server.app import create_app
from src.server.config import PASSWORD_ENV, ServerConfig, ServerConfigError, banner, resolve_config
from src.server.endpoint import EndpointPublisher
from src.server.events import EventHub
from src.server.ownership import OwnershipError
from src.server.runtime import ServerRuntime

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='sigil server',
        description='Serves the Sigil web client over HTTP, with a password gate.',
    )
    parser.add_argument('--host', help='address to bind (default 127.0.0.1; 0.0.0.0 for the LAN)')
    parser.add_argument('--port', type=int, help='port to bind (default 8760)')
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
    parser.add_argument('--reload', action='store_true', help=argparse.SUPPRESS)
    return parser


def run(argv: list[str] | None = None) -> int:
    """Starts the server. Returns the process exit code."""
    args = build_parser().parse_args(argv)

    # `.env` first, for the same reason the terminal client does it: the password can
    # live there, and reading it after the config would make it invisible.
    load_dotenv()

    try:
        config = resolve_config(host=args.host, port=args.port, password=args.password)
    except ServerConfigError as exc:
        print(f'sigil server: {exc}', file=sys.stderr)
        return 2

    if args.rebuild_ui:
        try:
            built = webbuild.build()
        except webbuild.BuildError as exc:
            print(f'sigil server: {exc}', file=sys.stderr)
            return 2
        print(f'sigil server: web client rebuilt ({built})')

    state = webbuild.staleness()
    if state.stale:
        print(f'sigil server: {state.message()}', file=sys.stderr)
        return 2

    # Imported here: uvicorn pulls in a lot, and `--help` should not pay for it.
    import uvicorn

    from src.engine.settings import load_settings

    settings = load_settings()
    composition = Composition(root=args.workspace, settings=settings)
    hub = EventHub()
    runtime = ServerRuntime(composition, hub)
    try:
        runtime.start()
    except OwnershipError as exc:
        print(f'sigil server: {exc}', file=sys.stderr)
        return 2
    publisher = EndpointPublisher()
    app = create_app(runtime, config, publisher=publisher)

    try:
        sock = listening_socket(config.host, config.port)
    except OSError as exc:
        print(f'sigil server: cannot listen on {config.host}:{config.port}: {exc}', file=sys.stderr)
        runtime.close()
        return 2

    print(banner(config), flush=True)
    print(flush=True)

    try:
        uvicorn.Server(
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
        ).run(sockets=[sock])
    except KeyboardInterrupt:  # pragma: no cover - the normal way to stop it
        pass
    finally:
        sock.close()
        runtime.close()
    return 0


def describe(config: ServerConfig) -> str:
    """The banner, exposed for tests that assert on what a user is told."""
    return banner(config)


__all__ = ['build_parser', 'describe', 'listening_socket', 'run']
