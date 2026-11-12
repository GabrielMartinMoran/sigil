# How Sigil is layered, and what the core owes a client

This page decides where the boundary between the core and a UI lies, what the
core exposes, and who owns a turn. It is the third piece of the same design:
[the domain](domain.md) fixes the words, [How Sigil stores its
state](storage.md) fixes the data, and this page fixes the layers.

The conclusion first: **the engine is already almost UI-agnostic; what has no
core is the application.** The work is not writing a service around an entangled
program — it is moving the orchestration that lives in the Textual mixins into a
facade, and letting the mixins become a client of it.

<!-- prettier-ignore -->
> [!IMPORTANT]
> This page describes a target, not the current state. **Phase 1 is done** — the
> storage change, [storage.md](storage.md) — and so is rollback, its natural
> consequence. The core itself, phases 2 to 4, is not: the orchestration still
> lives in the Textual mixins, and the contract below is a proposal to be
> refined before it is written.

## The evidence

Three measurements, each one pointing at the same seam.

| Observation | Number | Where |
| --- | --- | --- |
| `src/engine` imports of Textual | **2**, both in one file | `themes.py` uses `textual.color.Color` and `textual.theme.Theme` |
| Dependencies `SigilApp` is constructed with | **25** | `ui/app.py`, `SigilApp.__init__` |
| Direct calls from `src/ui` into `_store`, `_model_service`, `_summoner` | **29** | across `ui/*.py` |
| `src/engine` / `src/ui` size | 19,819 / 8,195 lines | — |

So the engine is not the problem. The problem is where the *application* lives:

- `turn_mixin.py` (555 lines) holds the turn loop, checkpoint throttling,
  steering, the abort window, and the title request.
- `tree_mixin.py` (475 lines) holds the wake-up policy: a 0.6 s debounce timer
  that starts a turn because subagents answered.
- `commands_mixin.py` (421 lines) holds session creation, loading, rebinding and
  persistence.

Three smells make the diagnosis concrete rather than stylistic:

1. **`Steer` has `widget: Static | None`** (`ui/turn_mixin.py:82`). A domain
   object holds a Textual widget. It cannot cross a transport, and it is the
   reason `Steer` is not in the engine today.
2. **`_wire_busy_flag`** (`ui/app.py:221`) reaches from the app *into*
   `ModelService` to install `is_busy = lambda: self._turn_active`. The service
   needs to know whether a turn is in progress; the UI is the only thing that
   knows; so the UI patches the service. A core would own that fact.
3. **The wake is polled, not announced.** `Summoner` knows it has unread answers
   (`waiting()`), and the UI asks on a timer. Inverted: the core should announce
   that an execution finished with something unread, and whoever is attached
   decides what to do with it.

## The boundary

| Belongs to the core | Belongs to the client |
| --- | --- |
| The turn loop: `agent.iter`, stream, checkpoint, recovery, step budget | Painting, keys, focus, layout, themes |
| Steering and abort as policy: what is accepted mid-turn, the double-`Esc` window, what happens to running subagents | The confirm panel that collects the second `Esc` |
| The session being driven: create, load, bind its workspace, choose profile, model and reasoning level | The picker and the modals that collect those choices |
| Wake policy: a finished execution with unread output starts a turn | The `↩ scout #abc sent its message` line, if a client wants it |
| Roadmap and tree mutations and queries | The panels that display them |
| Titles, previews, session listing | The window title, the status bar |
| Context meter, cost, cache | The `/context` rendering |

Nothing in the right column makes a decision. That is the test for a future
argument about where a piece of code goes: **if it decides, it is core; if it
shows, it is client.**

The split has a place where it shows up first: **the command palette**. It looks
like one list and it is two. `engine/commands.py` carries the catalogue with an
`owner` on every entry — `core` for the ones the domain answers, `client` for
the ones that only configure the window — so a second client reads the list,
keeps what it must delegate and brings its own for the rest. Without that field
the two were one flat list, and the difference was invisible until someone tried
to serve a command from outside the terminal. See [the domain](domain.md).

A second case is worth naming because it looks like a boundary violation and is
not: **a tool says what it did, in one line**
(`engine/tooling/presentation.py`). Only `patch_file` knows that "3
replacements" is the interesting fact and that `bytes_before` is not, so the
summary is declared next to the tool — the same reasoning `permission.py` gives
for its group vocabulary. What crosses the line is data: a short string and a
`good`/`bad`/`neutral`, never a color, a width or a glyph. The row that draws it
decides those.

The test has a second half, learned from the snapshot of
[rollback](rollback.md): **if it must happen even when nobody is looking, it is
core.** A checkpoint, a turn row, a tree snapshot and a rollback exist so that a
user can come back later. If a client owned them, closing that client would
silently stop the history — and a web client would have to reimplement them.

## The contract

The core is a facade over the aggregates plus a stream of events. Two rules keep
it from becoming a god object with a new name.

### Rule 1: commands in, events out

A command is a method call with a typed result or a typed error. An event is a
value that describes something that happened. **No getters that reach into the
store**, because a contract made of `get_*` is the mixin's state moved sideways.

There is no single object with every method. There are three services, one per
aggregate they orchestrate, and a `SigilCore` that composes them and owns
nothing else:

```python
class SessionService:          # the session and its workspaces
    def open_workspace(self, path: Path) -> Workspace: ...
    def new_session(self, *, worktree: str | None = None) -> SessionSummary: ...
    def load_session(self, session_id: str) -> SessionSnapshot: ...
    def list_sessions(self) -> list[SessionSummary]: ...
    def set_profile(self, session_id: str, profile: str) -> None: ...
    def set_model(self, session_id: str, choice: ModelChoice) -> None: ...
    def roadmap(self, session_id: str, execution_id: str) -> list[RoadmapItem]: ...
    def update_roadmap(self, ...) -> list[RoadmapItem]: ...
    def checkpoints(self, session_id: str) -> list[Checkpoint]: ...
    def rollback_to(self, session_id: str, turn_id: str, *, files: bool = True) -> RollbackOutcome: ...

class TurnService:             # one turn of one execution
    def start_turn(self, session_id: str, prompt: Prompt) -> str: ...  # turn_id
    def steer(self, session_id: str, turn_id: str, prompt: Prompt) -> None: ...
    def abort(self, session_id: str, turn_id: str) -> None: ...

class TreeService:             # the executions of a session
    def summon(self, session_id: str, execution_id: str, profile: str, brief: str) -> str: ...
    def send(self, session_id: str, execution_id: str, message: AgentMessage) -> None: ...
    def kill(self, session_id: str, execution_id: str) -> None: ...

class EventHub:                # fan-out and replay of what happened
    def subscribe(self, handler: EventHandler) -> Subscription: ...
    def publish(self, event: Event) -> None: ...
    def replay(self, session_id: str, execution_id: str, after_seq: int) -> list[Event]: ...

class SigilCore:               # composition and nothing else
    sessions: SessionService
    turns: TurnService
    tree: TreeService
    events: EventHub
```

`TurnService` and not `RunService`: the domain word is `Turn` and the library's
`run_id` never reaches a client. A caller holds a `turn_id`, steers with it,
aborts with it, and replays from it. See [the domain](domain.md).

The split is the guard against the god object this page lists as a risk: a
facade with twenty methods is the mixin's state moved sideways, while three
services with five methods each stay testable on their own. `SigilCore` is a
record of the three, plus the event stream they share.

`SessionSnapshot` is what a client needs to paint a resumed session: the
summary, the tree, the selection, the transcript of the root, and the **loaded
skills per execution** — a projection the core computes from the log, so a
client never has to remember them or restore them. It is a value, not a live
object: nothing in it changes under the client's feet.

### Rule 2: the database is the truth; events are the notification

An event says *what changed*, never *the whole state*. A client that missed
events asks for the state, and the answer comes from rows.

That is why the storage change comes first: with `seq` per execution, catching
up is `SELECT ... WHERE execution_id = ? AND seq > ?`, which is a range scan.
Without it, a reconnecting client has no way to ask "what did I miss" and the
events become the only source of truth — which means a dropped connection loses
the conversation.

### The event vocabulary

Ten kinds, all JSON. The existing pydantic-ai events are already pydantic
models, so this is a projection, not a new protocol.

| Event | Fields | Notes |
| --- | --- | --- |
| `turn_started` | `session_id`, `execution_id`, `turn_id`, `origin`, `at` | `origin` is `user`, `wake` or `internal` |
| `text_delta` | `turn_id`, `part_index`, `delta` | streaming |
| `thinking_delta` | `turn_id`, `part_index`, `delta` | streaming |
| `tool_started` | `turn_id`, `tool_name`, `tool_call_id`, `args` | args truncated if huge |
| `tool_finished` | `turn_id`, `tool_call_id`, `ok`, `bytes` | the result itself is read from the transcript, never pushed |
| `notice` | `session_id`, `level`, `text` | what the UI paints as a line |
| `execution_changed` | `session_id`, `execution_id`, `status` | an execution started, finished or failed |
| `answer_waiting` | `session_id`, `execution_id` | replaces the 0.6 s timer |
| `usage` | `turn_id`, `step`, `input`, `cache_read`, `cache_write`, `output`, `cost` | the meter's input |
| `turn_finished` | `turn_id`, `outcome`, `steps`, `reason` | `outcome` is `done`, `failed`, `cancelled`, `budget` |
| `rollback_started` | `session_id`, `target_turn_id`, `files` | before anything is touched |
| `rollback_files` | `session_id`, `restored`, `deleted`, `unavailable` | what the workspace half did, or why it could not |
| `rollback_finished` | `session_id`, `turns_dropped`, `messages_dropped`, `undo_checkpoint` | the outcome, including the way back |

`rollback_*` is a sequence and not one event because a rollback that fails
halfway must be reported as half done. A single `rollback_finished` with an
error would leave a client unable to say whether the files moved.

Deliberately absent: the message itself, the whole history, and tool results.
Those are read from the store, on demand, by the client that wants them. Pushing
them would duplicate the database on the wire and make the event log a second
source of truth.

## What a service changes about the product

This is a state-model change, not a refactor. It has to be named now, because it
decides how the facade is written.

| Today | As a service |
| --- | --- |
| One session per process: `SessionStore.rebind`, `AgentTreeStore.bind`, `RoadmapStores.bind_session` | Many sessions; every command carries `session_id` and the `bind` methods disappear |
| Closing the UI kills the turn | The turn survives the client; a client attaches to a turn in progress |
| `ask_user` assumes someone is looking | A question with no client attached must have a policy: wait, or fail with a clear reason |
| One process, one writer | One writer per turn, many readers: a written rule, and a partial unique index in the schema (`turns_one_open`) |

Those four are the reason the interface is worth doing; they are also the reason
it cannot be done as a layer over the current bind-based stores. The storage
change already removes the rebinding, because rows are addressed by id.

## Phases

Each phase ends with the app working and the tests passing.

1. **Storage, phases 1-4** ([storage.md](storage.md)). ✅ The delta write is the
   user-visible win, and it produces the `seq` that `replay` needs.
2. **`src/core/`**: `SessionService`, `TurnService`, `TreeService`, `EventHub`,
   with `Session`, `AgentTree` and `RoadmapRegistry` behind them. No behaviour
   change: the UI still drives, but through the facade, and `ui/turn_mixin.py`
   keeps only the rendering of what the events say.
3. **The wake becomes an event.** The 0.6 s timer moves into the core as a
   debounce over `answer_waiting`, which deletes the timer from the UI and one
   class of bug with it: a wake that starts a turn while the user is reading
   another execution.
4. **The UI as a client.** `turn_mixin`, `tree_mixin` and `commands_mixin`
   shrink to: receive event, paint; receive input, call command.
5. **Rollback and fork**, in the phases of [rollback](rollback.md). ✅ The
   commands, the git half, the `/rollback` screen and the click on a user
   message. The screen and the commands live in the UI for now: moving them
   behind the core is phase 2's work, like everything else.
6. **Transport**, if and when a second client exists: JSON-RPC over a pipe or
   HTTP+SSE over a socket, serializing the same commands and events. No second
   design; the interface already exists in process.

The order is not negotiable: **transport last.** A protocol designed before the
in-process boundary is a protocol shaped by what was convenient to serialize,
and the boundary is where the thinking is.

## Risks

| Risk | Why it bites | Mitigation |
| --- | --- | --- |
| `turn_mixin` is 555 lines with three intertwined responsibilities | Moving it without characterization tests is a bet | Write the tests **before** moving: `agent.iter` over a `FunctionModel`, a temp database, no Textual |
| A facade of getters | A god object that merely relocated the mixin's state | Commands and events only; a `get_*` that returns live state is refused in review |
| Two sources of truth | The event log and the database disagree after a crash | The database wins, always; events carry deltas and ids, never state |
| A service is an attack surface | A shell tool behind a socket | Local transport first; exposing it is a security decision with its own review |
| Concurrency | Two clients steering the same turn | One writer per turn, enforced by the schema (`turns_one_open`) and documented in [the domain](domain.md), invariant 5 |
| Rollback touching the user's files | The one command that writes outside `~/.sigil`, and filesystem work is not transactional | Snapshot first, restore files before truncating the conversation, and every step reversible from the `before_rollback` checkpoint ([rollback](rollback.md)) |

## What this page does not decide

- **The transport.** Named as the last phase, unspecified on purpose.
- **Whether the core is a separate process in the default setup.** The facade
  works in-process first; splitting it into a process is a deployment choice
  that the contract already allows.
- **How a web client authenticates.** Out of scope until there is one, and it
  should be a decision of its own rather than a side effect of the boundary.

## Durable server consumer (part A)

The web server owns its database through an exclusive OS lock in
`src/server/ownership.py`. A second cooperating server fails at startup with
`OwnershipError` instead of serving. Ownership is taken before reconciliation
and held until the process closes; legacy processes that never take the lock are
outside this boundary.

On startup, `ServerRuntime.start` marks every non-terminal accepted input as
`interrupted` without executing it. A consumer task then claims `accepted` rows
in sequence order and runs them through the normal `agent.iter` path. Inputs
accepted while a turn is running are handed to `run.enqueue` at an existing
steering seam; a steer that misses that seam, including the end-of-turn window,
is left visibly stopped and is never promoted to a later turn. The consumer
also uses a bounded poll, so an input accepted by another runtime is picked up
without requiring a new `send`.

Delivery is confirmed only inside a checkpoint: an `EnqueuedMessagesEvent`
forces a save whose confirmations name requests whose content is present in
`run.all_messages()`. The turn's own prompt is confirmed from the same snapshot.
A previous throttled checkpoint is not proof. Confirmed rows become `delivered`
and then `finished` only after the final save commits. Explicit stop marks
undelivered accepted/claimed rows `stopped`; shutdown marks non-terminal rows
`interrupted`. The pending overlay, recovery notice contract and web client are
not part of this slice.

Mid-turn recovery limit: a conflict during a throttled checkpoint saves the local
progress into a recovery copy and binds the UI to it, while the running turn keeps
its local session reference. The final save reconciles again and binds the winning
session. A second conflicting write in that window can therefore leave more than
one recovery copy; no transcript or delivery fact is merged across them. See
`ServerRuntime._checkpoint` and `ServerRuntime._turn`.

Abort and consumer death: abort sets `_aborting` so `_pump_steering` stops claiming
new work and `stop_open(keep=...)` does not stop requests already handed to
`run.enqueue`. Once the run unwinds, requests confirmed from its snapshot become
`finished`; the rest become `stopped`. A consumer that dies calls the
database-wide `interrupt_open`, so accepted rows become `interrupted` and `send`
refuses new work instead of accepting rows nobody will consume.

Durable read (B2): `GET /api/session/durable` calls `views.durable_session`, which
opens one SQLite read transaction and returns the messages rows as the transcript,
the accepted-input queue with state and sequence, `input_deliveries` facts,
recovery links with availability, a `pending` overlay, and `conflicted_entries`.
Pending means accepted or claimed without a delivery fact; delivered/finished rows
never reappear there and are read from the transcript. A deleted recovery copy is
reported unavailable without a broken link, and conflicted entries are kept
separate from the current transcript. Live deltas are not mixed into this read.
The web client is unchanged and not part of this slice.

### The turn command

`POST /api/turn` is the one way a message enters a session, and its shape is the
contract a retry depends on: `text`, an optional caller-generated `request_id`,
and `images`, a list of `{media_type, data}` where `data` is base64. The
position of an image in that list **is** its `[Image #n]` number in the text, so
the order is part of the message's identity. A client sends picture bytes; there
is no filename field and no path is ever opened, because a name in a JSON body
would be a request to read a file on the server.

`request_id` is the durable identity. The same id with the same source payload
— the same text and the same images as the client sent them — is the same
message: the response repeats `duplicate: true`, the same persisted image
identifiers, and nothing executes twice. The same id with a different payload
is a conflict and is refused. The identifiers in the response are the ones
stored, never regenerated, so a client can retry without losing track of images
it already sent.

The fingerprint covers the accepted text and the normalized image bytes and
media types, because `prepare_image` re-encodes every payload before it is
stored. That normalization is deterministic for a given input, so a retry of
the same original payload is idempotent, but it is not a fixed point for JPEG:
sending back bytes the server already normalized can produce different bytes
and is refused as a conflict, not recognized as a duplicate. The ids and
timestamps the server assigns are excluded from the fingerprint. See
`src/engine/storage/accepted_inputs.py`.

Validation happens in `ServerRuntime.decode_images` before the queue is touched,
so a refusal writes nothing:

| Refused | Limit |
| --- | --- |
| Too many images | 10 per message |
| Image too large | 4 MiB per image, checked after base64 decoding and before the image is decoded |
| Media type | only `image/png`, `image/jpeg`, `image/gif`, `image/webp` |
| Payload | must be valid base64 and must decode as an image |
| Request body | 64 MiB of JSON |

Images are re-encoded by `src/engine/attachments/images.py` before they are
stored, which bounds their dimensions and makes the stored media type the one
the bytes actually are.

The browser still sends text and a request id only; the image picker is not part
of this slice. `BodyLimitMiddleware` is registered inside `AuthMiddleware`, so
an unauthenticated request is answered without its body being read. For an
authenticated one, a declared `Content-Length` is refused before the body is
read, and a chunked body is counted as it arrives; either way the `413` is sent
without the route's parser ever seeing a complete body.

## The local endpoint (part B)

The server publishes where it listens, and with which token, in a file next to
the database. A local client reads that file instead of guessing a port. This
slice adds no route: the endpoints a client calls are the ones the browser
already uses.

| Item | Value |
| --- | --- |
| Directory | `$SIGIL_HOME/service`, mode `0700`, overridable with `SIGIL_SERVICE_DIR` |
| File | `endpoint.json` inside that directory, mode `0600` |
| Fields | `host`, `port`, `pid`, `token`, `started_at` |
| Token | `secrets.token_urlsafe(32)`, generated once per process |
| Written | From the application's lifespan, after the port is listening |
| Removed | When the same lifespan ends |

`src/engine/paths.py` owns both locations, like every other directory Sigil
writes to. `src/server/endpoint.py` owns the contract.

### Why TCP on loopback and not a UNIX socket

uvicorn creates a UNIX socket with mode `0666`, whatever the umask says
(`uvicorn.server.Server.startup`). A socket that every local user may open does
not separate users, so the isolation would have to come from somewhere else
anyway, and a `0666` socket is a false sense of one. A socket on a
Windows-mounted path under WSL does not work at all. TCP on `127.0.0.1` plus a
token in a `0600` file puts the barrier where it can be checked.

The limitation is stated rather than hidden: any local user who can reach the
port can send requests, and what they cannot do is read the token. The login
throttle still applies to the password.

### The file is published after the port listens

A file that names a closed port is worse than no file, so the order is part of
the contract. uvicorn's own order is the obstacle: `Server.startup` runs the
application's lifespan *before* it creates a listening socket. `src/server/cli.py`
therefore binds and listens itself (`listening_socket`) and hands the finished
socket to `uvicorn.Server(...).run(sockets=[socket])`. The lifespan publishes
once the kernel is already accepting connections, so a client that reads the
file immediately is queued instead of refused.

Publication is atomic: the payload goes to a sibling temporary file and is moved
into place with `os.replace`. A reader sees the previous publication or the next
one, never a truncated file, and the temporary is removed if the write fails.
Shutdown unpublishes before the runtime unwinds, so a client that finds the file
late fails to connect instead of attaching to a service that is stopping. A run
that finds a file with a different token leaves it alone: it belongs to the
service that replaced this one.

The ownership lock in `src/server/ownership.py` is untouched by any of this and
is still never unlinked, for the reason it already documents.

### What a reader trusts

Nothing in the file is authority. `read_endpoint` returns `None` for a missing
file, invalid JSON, a missing or mistyped field, a port outside `1-65535`, any
group or other permission bit on the file, a file owned by another user, and a
symlink. Every check is about the file: they say whether that JSON is a
well-formed endpoint, not whether the process behind it still exists.

A file left behind by `SIGKILL` therefore passes. It keeps its fields, its mode
and its owner, so `read_endpoint` returns it as a valid endpoint while nothing
listens on the port. Liveness is a different question and belongs to the
launcher in the following slice: it is the one that must confirm the `pid` is
alive, or the port answers, before attaching to what the file names. Until that
exists, a client that reads a stale file learns the truth when it connects, not
when it reads.

### The token is a second credential, not a second password

`AuthMiddleware` accepts `Authorization: Bearer <token>` from the endpoint file
next to the cookie the browser keeps. The protected set does not change: `/api/*`
needs a credential unless the path is free, and `/events` needs one too. A
missing, malformed or wrong token is the same `401` with the same
`unauthenticated` body as a missing cookie; the token is never repeated in a
response or a log line. See `src/server/auth.py`.

The token does not log into the web client: `Auth.check_password` compares it
against the password and refuses it. It also dies with the process, which is what
makes a restart a revocation.

Not in this slice: launching or discovering the service from the terminal, idle
shutdown, and the parity endpoints the terminal needs before it can become a
client. Those are separate, smaller steps.
