# The local endpoint (part B)

The server publishes where it listens, and with which token, in a file next to
the database. A local client reads that file instead of guessing a port. This
slice adds no route: the endpoints a client calls are the ones the browser
already uses.

| Item      | Value                                                                    |
| --------- | ------------------------------------------------------------------------ |
| Directory | `$SIGIL_HOME/service`, mode `0700`, overridable with `SIGIL_SERVICE_DIR` |
| File      | `endpoint.json` inside that directory, mode `0600`                       |
| Fields    | `host`, `port`, `pid`, `token`, `started_at`                             |
| Token     | `secrets.token_urlsafe(32)`, generated once per process                  |
| Written   | From the application's lifespan, after the port is listening             |
| Removed   | When the same lifespan ends                                              |

`src/core/config/paths.py` owns both locations, like every other directory Sigil
writes to. `src/server/launch/endpoint.py` owns the contract.

## Why TCP on loopback and not a UNIX socket

uvicorn creates a UNIX socket with mode `0666`, whatever the umask says
(`uvicorn.server.Server.startup`). A socket that every local user may open does
not separate users, so the isolation would have to come from somewhere else
anyway, and a `0666` socket is a false sense of one. A socket on a
Windows-mounted path under WSL does not work at all. TCP on `127.0.0.1` plus a
token in a `0600` file puts the barrier where it can be checked.

The limitation is stated rather than hidden: any local user who can reach the
port can send requests, and what they cannot do is read the token. The login
throttle still applies to the password.

## The file is published after the port listens

A file that names a closed port is worse than no file, so the order is part of
the contract. uvicorn's own order is the obstacle: `Server.startup` runs the
application's lifespan _before_ it creates a listening socket.
`src/server/cli.py` therefore binds and listens itself (`listening_socket`) and
hands the finished socket to `uvicorn.Server(...).run(sockets=[socket])`. The
lifespan publishes once the kernel is already accepting connections, so a client
that reads the file immediately is queued instead of refused.

Publication is atomic: the payload goes to a sibling temporary file and is moved
into place with `os.replace`. A reader sees the previous publication or the next
one, never a truncated file, and the temporary is removed if the write fails.
Shutdown unpublishes before the runtime unwinds, so a client that finds the file
late fails to connect instead of attaching to a service that is stopping. A run
that finds a file with a different token leaves it alone: it belongs to the
service that replaced this one.

The ownership lock in `src/server/launch/server_ownership.py` is untouched by
any of this and is still never unlinked, for the reason it already documents.

## What a reader trusts

Nothing in the file is authority. `read_endpoint` returns `None` for a missing
file, invalid JSON, a missing or mistyped field, a port outside `1-65535`, any
group or other permission bit on the file, a file owned by another user, and a
symlink. Every check is about the file: they say whether that JSON is a
well-formed endpoint, not whether the process behind it still exists.

A file left behind by `SIGKILL` therefore passes. It keeps its fields, its mode
and its owner, so `read_endpoint` returns it as a valid endpoint while nothing
listens on the port. Liveness is a different question, and the answer is
`live_endpoint` in [the launcher](service-launcher.md#the-launcher-part-c): it
confirms the `pid` still exists and that the port answers `/health` before
anything attaches to what the file names.

## The token is a second credential, not a second password

`AuthMiddleware` accepts `Authorization: Bearer <token>` from the endpoint file
next to the cookie the browser keeps. The protected set does not change:
`/api/*` needs a credential unless the path is free, and `/events` needs one
too. A missing, malformed or wrong token is the same `401` with the same
`unauthenticated` body as a missing cookie; the token is never repeated in a
response or a log line. See `src/server/api/auth.py`.

The token does not log into the web client: `Auth.check_password` compares it
against the password and refuses it. It also dies with the process, which is
what makes a restart a revocation.

Not in that slice: launching or discovering the service from the terminal, and
the parity endpoints the terminal needs before it can become a client. Launching
is [part C](service-launcher.md#the-launcher-part-c) and the stop-when-idle
behaviour is [idle shutdown](service-runtime.md#idle-shutdown); the parity
endpoints are [part D](service-commands.md#parity-commands-part-d).

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
