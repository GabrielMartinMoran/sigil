# The launcher (part C)

A client that needs a service calls `ensure_service` in
`src/server/launch/launch.py` and gets one of two things: a live endpoint to
talk to, or a named failure that says what to do next. "Start one if it is not
running" is not a sentence in the code either — it is the algorithm below,
because the interesting part is who is allowed to start it.

`Launch` carries the endpoint and, when this call started the process, the
`Child` handle for it. `Launch.launched` is true only when the endpoint's `pid`
is that child's: a candidate that spawned a process, lost the lock and attached
to another candidate's service did not launch anything.

## The algorithm

Called with a database path (normally `database_path()`), an optional command
and an optional endpoint path:

1. Read the endpoint file and prove it alive. `live_endpoint` is `read_endpoint`
   plus two facts: the recorded `pid` still exists, and a `GET /health` carrying
   the run token answers `200` within `probe_timeout` (0.5 s by default). A live
   endpoint is returned immediately — attaching is the common case.
2. Otherwise ignore the file and **do not delete it**. It may belong to a run
   that is publishing right now, or to one another client is about to read.
   Ignoring it is enough: the next publication replaces it with `os.replace`.
3. Ask the ownership lock whether the store is free, with
   `ServerOwnership.free`. It locks and lets go without writing: the lock is the
   only arbiter, and the `pid=` record it leaves behind is the evidence used two
   steps later. A probe that recorded itself would destroy that evidence.
4. Free ⇒ this call is the candidate. `spawn_server` starts the server, and the
   child takes the lock for real. The child acquires it in `cli.run` _before_ it
   composes the engine, so a candidate that loses the store exits there — before
   settings, MCP config, the skill registry or a port — and a lost race never
   produces a second listening server.
5. Not free ⇒ another process is starting one. Wait for its publication instead
   of starting a competitor. If the store stays free and the **recorded holder
   is gone**, nobody is coming back to publish, and that is a `ServiceDied`
   naming the pid rather than a wait until the deadline.
6. Either way, look at the endpoint file — and at the child, when there is one —
   every `interval` (50 ms) until `wait` expires (20 s by default). The first
   live endpoint wins and is returned.
7. If no endpoint appears, the deadline picks the failure from what was
   observed, not from a guess: see the table below.

Every call that returns has either an endpoint or a typed error, and no path
waits longer than `wait`. The deadline is a parameter so a caller can shorten
it, and every test passes its own.

## The typed failures

Callers receive a specific failure instead of inferring startup state from an
empty endpoint.

| Error                | `code`                | What was observed                                                                   | What the caller does                               |
| -------------------- | --------------------- | ----------------------------------------------------------------------------------- | -------------------------------------------------- |
| `ServiceStarting`    | `service_starting`    | Another process owns the lock and published nothing within `wait`                   | Retry: someone else is winning, slowly             |
| `ServiceDied`        | `service_died`        | The recorded owner of the lock is gone, or our own child exited, without publishing | Show the quoted output; a retry starts the service |
| `ServiceUnpublished` | `service_unpublished` | The child this call started is alive and published nothing                          | Keep the handle: wait, or `terminate()` it         |

`ServiceDied` quotes the child's own last words (up to 2 KB): a stale bundle, a
taken port and a refused nested invocation all end here, and the message is
already the fix. `ServiceUnpublished` carries the `Child` because a process that
is alive may still succeed; nothing here kills it on the caller's behalf.

An unsafe lock path is not one of these. `OwnershipError` propagates, because no
amount of waiting repairs a lock file that is a symlink.

## How the child is launched

`spawn_server` uses `subprocess`, and the command is
`[sys.executable, <checkout>/main.py, 'server']` (`default_command`):

- **`server`, not `serve`.** The child is the API surface: no bundle is mounted
  and no password is generated, so the credential the launcher reads from the
  endpoint file is the only one in play. There is no `--password` or
  `--rebuild-ui` in the argv because neither flag exists on that subcommand.
- **`sys.executable`, not the `sigil` wrapper.** The wrapper exists so a human
  can call it from any directory; this process already knows where the code it
  imported lives, and resolving `PATH` could start a server from a different
  installation than the endpoint file it is watching.
- **`close_fds=True`.** The child inherits the three standard streams and
  nothing else: no lock descriptor, no database handle, no event-loop
  descriptor.
- **`start_new_session=True`.** It is not in the launching terminal's process
  group, so `ctrl+c` there is not a shutdown here and no terminal hangup reaches
  it. The service deliberately outlives the client that started it; the
  ownership lock and the idle shutdown are what end it, not the window.
- **`stdin` is `/dev/null`; `stdout` and `stderr` share one anonymous temporary
  file.** A pipe would block the child once it filled, and the terminal would
  show a banner over a full-screen interface. The file is unbounded, invisible,
  read only to explain a death, and unlinked when the handle is closed.
- **Death is `poll()`, not a timeout.** The kernel reports it as it happens, the
  same call reaps it — so a watched child leaves no zombie — and the launcher
  reacts at the next `interval` instead of waiting out the deadline.

The environment is inherited unchanged. If `SIGIL_SELF_GUARD` is set — a client
running under the agent — the child refuses to start and this launcher reports a
`ServiceDied` carrying the child's own message. The guard is a rule of the
product, not an obstacle to route around, so it is not stripped. That is also
why the launch tests drive a process double: a real child that takes the same
ownership lock, publishes the same endpoint file and answers `/health` over a
real socket, so no test depends on the guard being absent.

## What this does not decide

The launcher deliberately leaves these concerns to the service and its caller.

- **The port.** The child publishes what it bound; the launcher reads it.
  Nothing here guesses or negotiates one.
- **When the service stops.** Not this call: the launcher starts a service, it
  never ends one. A service stops itself when no work is left anywhere in the
  database, as described in [idle shutdown](service-runtime.md#idle-shutdown) —
  a running turn or any non-terminal queue row keeps it up, and the end is a
  graceful exit that unpublishes the endpoint and releases the lock rather than
  a kill.
- **The engine.** `ensure_service` spawns a process; it does not compose one.
  The child builds its own `Composition`, and `sigil server` takes the lock
  _before_ building it: a losing candidate pays nothing for settings, MCP config
  or the skill registry. The runtime adopts that already-held lock (`ownership=`
  in `ServerRuntime`), so the lock is opened once and covers the whole life of
  the process — from before `Composition` to `close`. `flock` treats two
  descriptors of one process as two holders, so composing first and acquiring
  after would refuse the server its own store.

## The known limits

`/health` is unauthenticated, so it proves that a Sigil-like server answers on
that port, not that it is the run whose token is in the file; a port reused by a
different service is caught at the first authenticated call, not at discovery.

The start race has one window left, and it is stated rather than hidden. Two
candidates can both see a free lock if the first asks, releases, and the second
asks before the first child has taken it; both then spawn. **No lock descriptor
is passed to the child** in this cut, so the child's own `acquire` is what
decides, and the worst case is a losing child that leaves in `cli.run` the
moment the lock is refused — before it composes a `Composition`, before it binds
a port — one wasted process, never a second server listening. Closing the window
entirely means handing the locked descriptor to the child, which changes how a
process is launched (inherited fd, `pass_fds`, an ownership the child does not
take) and is not needed for correctness: the loser already pays nothing but its
own start-up.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
