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
| `ask_user` assumes someone is looking | A question has a deadline: `QUESTION_TIMEOUT_SECONDS`, overridable, after which it is withdrawn, the turn stops and the screen says why (see [the question deadline](#the-question-deadline)) |
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
undelivered accepted/claimed rows `stopped`. Interrupting is reserved for
reconciliation: `ServerRuntime.start` marks every non-terminal row `interrupted`
on the next start, and a consumer that dies does the same so nobody waits for
work that will never run. An orderly shutdown never interrupts — see
[idle shutdown](#idle-shutdown). The pending overlay, recovery notice contract
and web client are not part of this slice.

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

### Idle shutdown

A service nobody needs should not outlive the work, so the server stops itself
instead of waiting to be killed. `IdleShutdown` in `src/server/runtime.py` asks two
questions, in this order:

- **Is a turn running?** `ServerRuntime.busy`. A pending question needs no rule of
  its own: the agent asks from inside the turn that is waiting for the answer, so the
  turn is still running and the question is covered. There are no interactive
  approvals (see [Risks](#risks)).
- **Is any row still open?** `AcceptedInputs.has_open`, which is database-wide:
  `accepted`, `claimed` or `delivered`, in *any* session, because one service serves
  the whole database. A row another browser accepted is work this process must not
  stop in the middle of.

Connected event subscribers are deliberately **not** work. A page reconnects to
whatever service is there, so a tab left open must not keep a process alive for ever:
`/events` is a view, not a claim on execution.

While work exists the service stays up, however much time passes. When it does not,
the window starts: `--idle-grace` seconds (900 by default, `DEFAULT_IDLE_GRACE`) with
no work expires it, and one poll that sees work drops the window and starts it again
later, so a burst of short turns never adds up to a shutdown. The poll is
`IDLE_POLL_INTERVAL` (0.5 s), and the question it asks is one lookup rather than a
scan: `AcceptedInputs.has_open` is a single statement over `accepted_inputs_open`, the
partial index this cut added over exactly the three open states (see
[the storage page](storage.md#schema-steps) for the measurement that motivated it).
A work check that raises is not proof of idleness: the watch keeps serving and says so
once per streak, because a service that dies of a store hiccup is worse than an extra
window.

The stop itself is graceful and does nothing else. `ServerRuntime.stop_when_idle`
calls the one request it was given — `GracefulStop.request`, bound in
`src/server/cli.py` to uvicorn's own `should_exit`. The process is never killed and
the endpoint is never removed by hand: uvicorn leaves its loop, the lifespan shutdown
runs in its normal order (watch cancelled, endpoint unpublished by
`EndpointPublisher.clear`, runtime closed, ownership lock released), and the next
client that asks for a service finds the store free and launches a new one.

Idle shutdown never relabels a row at the moment it leaves. A request can be accepted
between the last idle look and the loop leaving, and `ServerRuntime.close` releases the
lock and closes the composition without touching the queue, so that row keeps the state
it has (`accepted`) instead of being written as executed, delivered or interrupted. That
is the honesty this path owes: the exit reports what it did, which is nothing to that
row.

It does not save the row from the label. The row was never executed, and the next
start's reconciliation (`ServerRuntime.start`) marks every non-terminal row
`interrupted` — correctly, because that is what happened to it. What the exit avoids is
the *false* claim, made by the process itself while it was still serving, that the row
was lost to a crash; the label that follows is a statement about execution, and only
reconciliation is entitled to make it.

Durable read (B2): `GET /api/session/durable` calls `views.durable_session`, which
opens one SQLite read transaction and returns the messages rows as the transcript,
the accepted-input queue with state and sequence, `input_deliveries` facts,
recovery links with availability, a `pending` overlay, and `conflicted_entries`.
Pending means accepted or claimed without a delivery fact; delivered/finished rows
never reappear there and are read from the transcript. A deleted recovery copy is
reported unavailable without a broken link, and conflicted entries are kept
separate from the current transcript. Live deltas are not mixed into this read.
A session that exists only in memory has no row to project, and the read answers
that with a typed `404` — "nothing yet", not a failure. The web client treats it
as the normal state it is: it clears the overlay instead of leaving the previous
conversation's queue painted over an empty one, and says nothing about it.

### A turn that is waiting says so

A turn can be slow without being broken, and the service used to be unable to tell
you the difference. The engine already retried transient model failures — five
attempts with a backoff of 0.5 to 8 seconds, see
`src/engine/agents/retry.py` — but only the in-process client listened for the
notice: `SigilApp.notify_model_retry` paints every attempt in its chat. In the
service that callback had no subscriber, so a provider that kept failing published
nothing at all and the screen read "Thinking" for minutes.
`ServerRuntime` now subscribes `RetryTransientModelErrors.on_retry` and publishes
what the terminal would have painted.

The event is the one both clients already know: `notice` with `tone` set to `warn`.
Its text is the terminal's own sentence, so the two surfaces say the same thing
about the same wait — the attempt that failed, the ceiling, the reason and the
delay, as in `↻ Transient model failure (HTTP 502 · …); retry 2/5 in 0.8s`. Nothing
new is asked of a client: the web store and the attached terminal already paint a
`notice`, and the sink is wrapped so that a client which cannot be told is not a
reason to fail the retry it describes.

Silence gets a sentence too, and never a deadline. `SLOW_TURN_NOTICE_SECONDS` (60)
is how long a turn may publish nothing before `ServerRuntime._watch_turn_silence`
says it is still waiting. The watcher is a task of the turn, cancelled with it,
which waits on an event every published event sets: a turn that is streaming, or
retrying, never reaches the notice, and a turn that ends before the window passes
never says it. It fires at most once per turn and it cancels nothing. That is the
decision this cut records: **there is no turn deadline**. A model that takes
minutes is legitimate, `agent.iter` runs with `UsageLimits(request_limit=None)`,
and only an explicit stop, the question deadline, or the step budget ends a turn
early.

A turn that ends in error closes its rows, exactly as a cancelled one does.
`ServerRuntime._turn` calls `_stop_pending` when the status is `error`, so the
accepted input cannot stay `claimed` until the next start interrupts it — which is
what used to happen, and what made a failed turn look like a lost one. The terminal
state is `stopped`, not a new one: `_TRANSITIONS` in
`src/engine/storage/accepted_inputs.py` allows `claimed → stopped` and has no
failure state, while `interrupted` stays reserved for reconciling a process that
died, as [idle shutdown](#idle-shutdown) states.

### The transcript, one window at a time

`GET /api/session/transcript` answers a conversation that grows, so it takes a
window instead of always sending the whole thing:

| Parameter | Meaning |
| --- | --- |
| `limit` | how many items to return; absent means the whole transcript |
| `before` | the position the page ends just before; absent means the newest page |

The reply carries `items`, `first_index` — the absolute position of the first item
of the page — `has_more`, whether anything older exists, `total`, the number of
items in the whole transcript, and `pending`, the rows waiting in the queue. Those
are the only fields paging adds; the items themselves are the projection
`views.transcript` already defined.

Three rules make the cursor usable:

- **Positions, not message indexes.** The `index` of an item is the message it was
  built from, and several items can share one — a message with a thought and a
  tool call. A cursor expressed in message indexes could not be chained without
  repeating a message or dropping part of it, so `before` and `first_index` are
  positions in the flat list. Asking with `before=first_index` returns exactly the
  chunk above the page: no hole, no repeat.
- **An item keeps its index.** A page never renumbers an item, because a click on
  a message maps its `index` to a rollback point. A tramo that renumbered would
  send the reader to the wrong turn.
- **A queued message is not history.** The rows waiting in the queue travel with
  the newest page only — the read asked for without `before`. They are not items,
  they do not count in `total`, and they never move a cursor. An older page answers
  with an empty `pending`: a row that is not history has no place in a chunk, and
  sending it with every page would paint it more than once.

The wait is one rule and not two. `views.waiting` decides which rows still wait —
accepted or claimed, and never delivered — and both reads that publish the queue
call it: `views.pending_rows` for the newest page and `views.durable_session` for
[the durable read](#durable-server-consumer-part-a). A screen and a recovering
consumer therefore cannot be told different things about one waiting message.

Compatibility is deliberate: without `limit` the answer is the whole transcript
with `first_index` 0 and `has_more` false, which is what a client that has not
migrated still gets. The window size — 100 items — is the client's decision to
send, not a default the service imposes. An unusable `limit` or `before` is a
`400` with a sentence: a limit that is not a positive whole number or is above
`MAX_TRANSCRIPT_PAGE` (1000), a cursor that is not a non-negative whole number.

Implementation: `views.transcript_page` is the only place the rule lives — it takes
the items already built and returns a `TranscriptPage`; the route validates the two
parameters and projects. See `tests/test_server_runtime.py` for the arithmetic and
`tests/test_server_parity.py` for the query contract.

The terminal is the first client to use it. `ChatLog` keeps at most
`MAX_MOUNTED_PAGES` (3) pages mounted. It asks for the newest page with
`limit=transcript_window` — 100 items by default, the `transcript_window`
preference in `ui.json` — and when the reader reaches the top it asks for the page
above with `before=` the absolute position its oldest mounted page starts at. That
insertion lands above the viewport, so the scroll is moved by exactly the height it
added: the widget is mounted with `mount(before=0)`, and `call_after_refresh` then
puts the row the reader was looking at back on its screen row with
`scroll_to(immediate=True)`. What they were reading does not move.

Past the bound the page furthest from the reading position is let go, not the page
held longest: what has to stay is what the screen is showing. The position is taken
before the page above is inserted — the insertion moves the scroll offset, not the
row the reader is on — and turned into a position among the mounted items, because
items are what a page is counted in and a tool block is not as tall as a line of
text. It gives two outcomes, one for each way a reader arrives at the bound. A
reader at the top is furthest from the newest page, so that one leaves and the
window stops ending where the conversation ends. A reader who came back down is
furthest from the page at the top, which is the page the cursor can ask for again.
Either way the page that leaves is the one the viewport shows least of, so nothing
on screen moves. The block being streamed into and the queue's waiting lines belong
to no page and are never destroyed; a page that somehow held one is skipped, and the
window grows past its bound rather than losing what is live. A waiting line stops
being live as soon as the app retires it — the run read it, or its row stopped being
pending — and `ChatLog.retire_pending` is what says so: `_is_live` reads that list, and
a line left in it after it was taken off the screen would keep a removed widget alive
while vouching for nothing that is painted.

What leaves above is asked for again with the cursor it carried, so history is never
lost — only unmounted. What leaves below cannot be: the route pages backwards only,
and there is no `after` cursor to walk down with. So a window that no longer ends
where the conversation ends — `_reaches_end` false in `ChatLog`, `reachesEnd` on the
web — reads the newest page again, the same read the screen opened with, as soon as
the reader reaches the bottom. Once per arrival: while that read is in flight, and
after it lands, the window ends the conversation again and a reader who keeps
scrolling at the bottom asks for nothing more. The anchor goes back to the bottom
with it, because the bottom is what they asked for. See `tests/test_ui_client.py`
for the ten cases: the window a long session mounts, the cursor a scroll asks for,
the row that does not move, the second page, the bound and the live block, the newest
page a reader at the top lets go of, the newest page read again at the bottom, the
page at the top a reader near the bottom gives up, the click that still names its
turn, and the window preference that falls back and clamps.

The web client is the second, and it holds the same bound with a different
mechanism. `web/src/transcriptWindow.ts` is the whole rule, as pure functions over
plain data: `newestWindow` makes the newest page the window, `withOlderPage`
prepends the page above with its cursor, `reachesEnd` answers whether the bottom of
the window is still the end of the conversation, and `wantsOlderPage` and
`wantsNewestWindow` decide when a scroll event has earned a read. It is pure so the
rule is testable without a store, a request or a DOM — `web/src/transcriptWindow.test.ts`
pins both eviction directions and both triggers. The store keeps the window in
`items` plus the `firstIndex` cursor, the page sizes, the `total` and the reader's
position, and `web/src/Transcript.tsx` only reports what only the DOM knows: where
the scroll is, and when it reached an edge. The size is `TRANSCRIPT_WINDOW` (100),
mirroring `DEFAULT_TRANSCRIPT_WINDOW`, because the web client has no settings
surface of its own.

That reported position has one unit: a fraction of the scroll, 0 at the top of the
window and 1 at the bottom, held in the store as `readingFraction`. It is not an item,
because what the component measures is pixels and what the eviction is counted in is
items — two units with no conversion is the defect this replaced: a position out of a
thousand was handed to `withOlderPage` as an index, so a reader below the first tenth
of a 300-item window read as "at the bottom" and the page being read was the page let
go of. `readingIndex(fraction, itemCount)` in `transcriptWindow.ts` is the only place
the two meet, which is why the mismatch cannot come back silently. The component
reports on every scroll event, before the read that event may ask for, and again after
restoring the scroll offset when a page lands above — the reader is where the insertion
left them, not where they were before it.

The reading position is what an eviction is measured against, and that is the
difference from a window that simply keeps the newest page. `withOlderPage` computes
the distance from the reader to the nearest edge of each mounted page and lets go of
the furthest one — the page held longest when the reader is at the top, and the page
at the top when they have come back down and kept reading lower. Ties go to the
front, because the top of the window is the side a cursor can walk back into. The
streaming block and the queue's waiting rows belong to no page: they are painted
from the store's `stream` and from the durable read, never from `items`, so a page
that leaves cannot take either with it.

What leaves the bottom has to come back a different way, because the route pages
backwards only: there is no `after` cursor. So when the reader reaches the bottom of
a window that no longer ends at the end of the conversation — `reachesEnd` false —
the client asks for the newest window again with the same read the screen opened
with, `refetchTranscript({ keepStream: true })`, rather than inventing a forward
cursor. `keepStream` is explicit because that read can land in the middle of a turn:
the deltas already received are the answer being written, and dropping them would
blank the block until the next one arrived. Only the reload that runs where the turn
has ended — `turn_finished`, a lagged stream, an opened session — clears the stream,
because there the transcript is the record. Between the two edges the screen holds at
most `MAX_MOUNTED_PAGES` (3) pages: at most 300 items of a conversation that may hold
thousands, and no read of the whole thing at any point.

An insertion above the viewport moves the scroll by exactly the height it added:
the component records `scrollHeight` and `scrollTop` before asking and, once the
window has grown, restores `scrollHeight - (height - top)` — the same row on the
same screen line. What a reader was reading does not move, and the auto-scroll that
follows a streaming turn is only applied to a reader who was already at the bottom.
The scroll wiring itself is not automated: this repository has no DOM test
environment, so `Transcript.tsx` is covered by the pure predicates it calls and by
review, and the comparison is made by hand in a browser.

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
listens on the port. Liveness is a different question, and the answer is
`live_endpoint` in [the launcher](#the-launcher-part-c): it confirms the `pid`
still exists and that the port answers `/health` before anything attaches to what
the file names.

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

Not in that slice: launching or discovering the service from the terminal, and the
parity endpoints the terminal needs before it can become a client. Launching is
[part C](#the-launcher-part-c) and the stop-when-idle behaviour is
[idle shutdown](#idle-shutdown); the parity endpoints are
[part D](#parity-commands-part-d).

## The launcher (part C)

A client that needs a service calls `ensure_service` in `src/server/launch.py` and
gets one of two things: a live endpoint to talk to, or a named failure that says
what to do next. "Start one if it is not running" is not a sentence in the code
either — it is the algorithm below, because the interesting part is who is
allowed to start it.

`Launch` carries the endpoint and, when this call started the process, the `Child`
handle for it. `Launch.launched` is true only when the endpoint's `pid` is that
child's: a candidate that spawned a process, lost the lock and attached to another
candidate's service did not launch anything.

### The algorithm

Called with a database path (normally `database_path()`), an optional command and
an optional endpoint path:

1. Read the endpoint file and prove it alive. `live_endpoint` is `read_endpoint`
   plus two facts: the recorded `pid` still exists, and a `GET /health` carrying
   the run token answers `200` within `probe_timeout` (0.5 s by default). A live
   endpoint is returned immediately — attaching is the common case.
2. Otherwise ignore the file and **do not delete it**. It may belong to a run that
   is publishing right now, or to one another client is about to read. Ignoring it
   is enough: the next publication replaces it with `os.replace`.
3. Ask the ownership lock whether the store is free, with `ServerOwnership.free`.
   It locks and lets go without writing: the lock is the only arbiter, and the
   `pid=` record it leaves behind is the evidence used two steps later. A probe that
   recorded itself would destroy that evidence.
4. Free ⇒ this call is the candidate. `spawn_server` starts the server, and the
   child takes the lock for real. The child acquires it in `cli.run` *before* it
   composes the engine, so a candidate that loses the store exits there — before
   settings, MCP config, the skill registry or a port — and a lost race never
   produces a second listening server.
5. Not free ⇒ another process is starting one. Wait for its publication instead of
   starting a competitor. If the store stays free and the **recorded holder is
   gone**, nobody is coming back to publish, and that is a `ServiceDied` naming the
   pid rather than a wait until the deadline.
6. Either way, look at the endpoint file — and at the child, when there is one —
   every `interval` (50 ms) until `wait` expires (20 s by default). The first live
   endpoint wins and is returned.
7. If no endpoint appears, the deadline picks the failure from what was observed,
   not from a guess: see the table below.

Every call that returns has either an endpoint or a typed error, and no path waits
longer than `wait`. The deadline is a parameter so a caller can shorten it, and
every test passes its own.

### The typed failures

| Error | `code` | What was observed | What the caller does |
| --- | --- | --- | --- |
| `ServiceStarting` | `service_starting` | Another process owns the lock and published nothing within `wait` | Retry: someone else is winning, slowly |
| `ServiceDied` | `service_died` | The recorded owner of the lock is gone, or our own child exited, without publishing | Show the quoted output; a retry starts the service |
| `ServiceUnpublished` | `service_unpublished` | The child this call started is alive and published nothing | Keep the handle: wait, or `terminate()` it |

`ServiceDied` quotes the child's own last words (up to 2 KB): a stale bundle, a
taken port and a refused nested invocation all end here, and the message is
already the fix. `ServiceUnpublished` carries the `Child` because a process that
is alive may still succeed; nothing here kills it on the caller's behalf.

An unsafe lock path is not one of these. `OwnershipError` propagates, because no
amount of waiting repairs a lock file that is a symlink.

### How the child is launched

`spawn_server` uses `subprocess`, and the command is
`[sys.executable, <checkout>/main.py, 'server']` (`default_command`):

- **`sys.executable`, not the `sigil` wrapper.** The wrapper exists so a human can
  call it from any directory; this process already knows where the code it imported
  lives, and resolving `PATH` could start a server from a different installation
  than the endpoint file it is watching.
- **`close_fds=True`.** The child inherits the three standard streams and nothing
  else: no lock descriptor, no database handle, no event-loop descriptor.
- **`start_new_session=True`.** It is not in the launching terminal's process
  group, so `ctrl+c` there is not a shutdown here and no terminal hangup reaches
  it. The service deliberately outlives the client that started it; the ownership
  lock and the idle shutdown are what end it, not the window.
- **`stdin` is `/dev/null`; `stdout` and `stderr` share one anonymous temporary
  file.** A pipe would block the child once it filled, and the terminal would show
  a banner over a full-screen interface. The file is unbounded, invisible, read
  only to explain a death, and unlinked when the handle is closed.
- **Death is `poll()`, not a timeout.** The kernel reports it as it happens, the
  same call reaps it — so a watched child leaves no zombie — and the launcher
  reacts at the next `interval` instead of waiting out the deadline.

The environment is inherited unchanged. If `SIGIL_SELF_GUARD` is set — a client
running under the agent — the child refuses to start and this launcher reports a
`ServiceDied` carrying the child's own message. The guard is a rule of the product,
not an obstacle to route around, so it is not stripped. That is also why the launch
tests drive a process double: a real child that takes the same ownership lock,
publishes the same endpoint file and answers `/health` over a real socket, so no
test depends on the guard being absent.

### What this does not decide

- **The port.** The child publishes what it bound; the launcher reads it. Nothing
  here guesses or negotiates one.
- **When the service stops.** Not this call: the launcher starts a service, it never
  ends one. A service stops itself when no work is left anywhere in the database, as
  described in [idle shutdown](#idle-shutdown) — a running turn or any non-terminal
  queue row keeps it up, and the end is a graceful exit that unpublishes the endpoint
  and releases the lock rather than a kill.
- **The engine.** `ensure_service` spawns a process; it does not compose one. The
  child builds its own `Composition`, and `sigil server` takes the lock *before*
  building it: a losing candidate pays nothing for settings, MCP config or the
  skill registry. The runtime adopts that already-held lock (`ownership=` in
  `ServerRuntime`), so the lock is opened once and covers the whole life of the
  process — from before `Composition` to `close`. `flock` treats two descriptors of
  one process as two holders, so composing first and acquiring after would refuse
  the server its own store.

### The known limits

`/health` is unauthenticated, so it proves that a Sigil-like server answers on that
port, not that it is the run whose token is in the file; a port reused by a
different service is caught at the first authenticated call, not at discovery.

The start race has one window left, and it is stated rather than hidden. Two
candidates can both see a free lock if the first asks, releases, and the second
asks before the first child has taken it; both then spawn. **No lock descriptor is
passed to the child** in this cut, so the child's own `acquire` is what decides, and
the worst case is a losing child that leaves in `cli.run` the moment the lock is
refused — before it composes a `Composition`, before it binds a port — one wasted
process, never a second server listening. Closing the window entirely means handing
the locked descriptor to the child, which changes how a process is launched
(inherited fd, `pass_fds`, an ownership the child does not take) and is not needed
for correctness: the loser already pays nothing but its own start-up.

## Parity commands (part D)

The terminal already had five commands that never reached HTTP: change the model,
change the effort, switch the agent, roll back, and fork. Without them a client
can watch a session and cannot drive one, so they are the last contract the
terminal needs before it becomes a client rather than an executor. This part adds
the routes and the shared decision behind them; it does not migrate the terminal.

### One decision, two transports

The command logic does not live in a route. Each route calls a `ServerRuntime`
method, and each of those calls the same engine object the terminal already calls:

- **Model and effort.** `ModelService.select` and `ModelService.set_thinking`. The
  service already refuses while a turn runs; `ServerRuntime` points
  `model_service.is_busy` at its own `busy` flag, so the HTTP command gets that
  `BusyError` instead of a second rule written in a route.
- **Agent.** `AgentRegistry.activate` plus `Composition.select_agent_model`.
- **Rollback and fork.** `composition.rollback`, the instance the terminal's
  picker uses.

The state a selection leaves behind is recorded once, in
`Composition.apply_model_change` and `Composition.apply_agent` — the session's
selection, the meter's window, `save_progress` when the conversation has messages,
the stamped agent, and the tree root's name. Both clients will call those; for this
cut only the server does.

The terminal's `ProviderMixin._after_model_change` and `AgentMixin.apply_agent`
still carry their own copy of those lines. That is deliberate and temporary: the
terminal is the only client in use, it is not migrated yet, and rewriting a path
this part does not exercise would be an unverified change to it. The copies go in
`tui-attach-v1`, when the terminal stops mutating state locally.

`Composition.rebuild_agent` is the server's responsibility after a model change,
because the agent closes over the model it was built with. It is not part of
`apply_model_change`: whether a client needs it depends on whether that client
holds its own agent, which is a fact about the client.

### The routes

| Route | Does | Reply |
| --- | --- | --- |
| `POST /api/model` | Switches account, model and optionally the level | `views.model_view` |
| `POST /api/effort` | Sets the level of the current model | `views.model_view` |
| `POST /api/agent` | Activates a profile for the next turn | `views.agent_view` plus the list |
| `GET /api/rollback` | Lists the turns the session can return to | `views.rollback_points` |
| `POST /api/rollback` | Returns to before a turn, rows and optionally files | outcome plus the session view |
| `POST /api/fork` | Branches a new session at an instant, destroying nothing | `views.fork_view` |

Every reply is a projection from `src/server/views.py`, and the parity contract in
`tests/test_server_parity.py` compares the reply to that projection rather than to
a literal, so a route cannot invent a shape the views module does not define.

### What is refused, and with which status

The refusals are the in-process ones, carried over HTTP rather than re-decided:

- **`409`** when the session is in the way and the same request would work later:
  a turn is running (`BusyError` for model and effort, the runtime's own check for
  agent, rollback and fork), or the named turn is not a point of this session
  (`RollbackError`).
- **`400`** when the request itself names something unusable and a different
  request would be needed: an unknown account or profile, a level the model does
  not accept (`ProviderError`, `AgentNotFound`).

A refusal changes nothing. The tests assert that, reading the session, the model
snapshot or the message rows after the rejected call rather than trusting the
status code.

### Not decided here

The terminal migration, its five mixins, and removing the duplicate selection
logic are `tui-attach-v1`. The transport that cut consumes is
[part F](#the-terminals-transport-part-f). Tree actions, subagent transcripts, the
MCP toggle, external files and context are `parity-extra-v1`, in the next section;
debug and themes stay in the terminal, because they are about a client, not a
session.

## Parity commands (part E)

Part D gave a client the commands that change what the next turn runs on. What it
left out is what the terminal draws around the chat: the tree of agents, the
transcript of one subagent, the two session toggles, and the context meter. This
part adds those six routes. Theme selection and the `/debug` report are still
client work, so they are in no route here.

### One decision, two transports

The commands are thin for the same reason as part D's: a route calls a
`ServerRuntime` method, and the method calls the object that already decides.

- **The tree.** `SubagentSummoner.detach` and `SubagentSummoner.kill`, reached
  through `Composition.background_subagent` and `Composition.stop_subagent`. The
  summoner is the object the terminal's `tree_mixin` calls, so the refusals are
  the engine's own: an unknown id, a node that already finished, and the session's
  own agent. The root rule lives in the summoner on purpose — `agent_tree_screen`
  lists every node and binds `b` and `k` on the highlighted row, the root
  included, so a guard in one caller would leave the action reachable from the
  other. `detach` and `kill` answer the root with the same sentence.
- **The toggles.** `Composition.apply_mcp_override` and
  `Composition.apply_external_files`, the same pair `commands_mixin` performs.
  `mcp_overrides` and `allow_external_files` are columns of the session row, so
  the change is written with `SessionStore.save_current`: a concurrent writer's
  copy is adopted instead of becoming a server error, and an empty draft writes
  nothing.
- **The context.** `Composition.meter` and `session_cost`, the two objects the
  terminal's sidebar reads. Nothing is computed in the route beyond the
  projection.

### The routes

| Route | Does | Reply |
| --- | --- | --- |
| `POST /api/tree/stop` | Stops a node and everything it summoned | `views.node_view`, `views.tree_view`, the engine's sentence |
| `POST /api/tree/background` | Sends a running node to the background | the same three |
| `GET /api/tree/node/{node_id}` | One execution's messages, by id or unique prefix | `views.node_view(…, include_messages=True)` |
| `POST /api/mcp` | Enables or disables one server for the session | `views.session_view` plus the shell's MCP state |
| `POST /api/external-files` | Allows or denies reads outside the workspace | `views.session_view` |
| `GET /api/context` | Window usage and the session's bill | `views.context_view` |

`/context` in an attached terminal paints that projection and the workspace from
`/api/state`. It shows no scope section — the active directories and the instruction
files have no projection on the server, so the report omits them rather than filling
them from local trackers, which would describe a session the service is not running.
A datum the service did not send is named as absent; it is never printed as zero,
because a zero is a claim. A cold session is the exception that proves the rule: the
service reports the real zero there, so the screen reads `0 (0%)` and never `?`.

The tree actions answer with the whole tree, not only the node: stopping a node
stops its descendants, and a client that repainted one row would leave the branch
saying it is still working.

The two toggles are also readable from `/api/state`: `views.session_view` carries
`external_files` and `mcp_overrides`, because the row is where they live and a
command that changed one has to be visible in the projection the screen paints
from, not only in its own reply.

### What is refused, and with which status

- **`400`** when the request names something that does not exist: a node
  reference that matches nothing, or an MCP server the config does not declare.
  `ServerRuntime.node` translates the summoner's `SummonError` into a
  `LookupError` for exactly this distinction. A body of the wrong type is `422`
  from the schema; `allow` is a `StrictBool`, because a lax bool would read
  `"yes"` as consent to a permission change.
- **`409`** when the node exists and cannot take the action now: the session's own
  agent, or a node that already finished (`SummonError`). The two toggles are
  `409` too while a turn runs, and `/api/mcp` is also `409` while a subagent is
  still working — the terminal's two criteria, not a new rule. A live node runs on
  an agent whose tools were computed when it started, so disabling a server would
  close the transport under a call already in flight and enabling one would swap
  the tool set mid-run. Both live where the decision is: the turn check is
  `ServerRuntime._refuse_while_busy`, the subagent check is
  `Composition.apply_mcp_override` reading `summoner.running()`, because the
  terminal's copy of that rule lives in `commands_mixin.is_refused_while_busy` and
  the engine is where both clients can share it.

Every route that refuses through `_refuse_while_busy` says the same thing, in one
place: `A turn is running: finish or stop it before you <action>.` Starting a
session, opening one, switching workspace, switching agent, rollback and fork all
go through it, so a client never has to parse a second wording for those.

Two routes are the exception, and it is part D's: `POST /api/model` and
`POST /api/effort` are refused by the engine itself — `BusyError` from
`ModelService.select` and `set_effort` — with `A turn is in progress. Wait for it
to finish (or stop it) before changing the model.` The status is the same `409`;
the caller tells them apart by the route it called, not by the sentence, and
unifying the wording was not part of that cut.

One asymmetry is the engine's and is carried over, not invented: `kill` on a
finished node *reports* that it had already finished and changes nothing, so that
request is a `200` carrying the sentence; `detach` refuses it, so backgrounding a
finished node is a `409`.

A refusal changes nothing. The tests assert it by comparing `views.tree_view` or
the session row before and after, not by trusting the status code.

### The context meter: what exists, and what does not

`ContextMeter.snapshot` and `session_cost` are enough for what the sidebar paints:
the **projected** usage, its percentage of the window, the budget split and the
bill. The projection keeps the terminal's rule that a cold session with a known
window is at `0%` rather than `?`.

Two honest limits travel with it:

- **The fixed blocks are a measurement, not a constant.** `tools` and
  `instructions` come from `ModelRequestParameters`, which exists only while a
  request is being made. Their last value is stamped on the session and
  `Composition` now restores it on construction and on `bind_session`, so the
  budget of a loaded session is not optimistic. The terminal's own `bind_session`
  still carries the same call; that copy goes in `tui-attach-v1`, when the
  terminal stops binding state locally.
- **The history is the checkpointed one.** The turn loop checkpoints about once a
  second, so during a turn the newest steps can be a moment behind the transcript
  the stream is painting. The route says `measured` and `projected` as they are
  rather than projecting a history the server does not have yet.

The `/debug` report is not here and does not need to be: it describes the request
the client is inspecting, with its cache and compaction decisions, and those are
per-request objects rather than session facts. Themes are presentation. `goto` is
navigation inside a tree the client already has; none of the three changes what
the next turn runs on.

### What is still duplicated

`views._turn_preview` and the terminal's `rollback_mixin._first_line_of_turn`
build the same line from the same engine call, `prompt_of_turn`: the prompt that
opened a turn, collapsed to one line and cut, with the image count appended. They
stay two copies for this cut for the same reason part D's selection copies do —
the terminal still owns its rendering and is not migrated yet, and a shared helper
would need `rollback_mixin` to consume it in the same change or it would only add
a third copy. The unification belongs to `tui-attach-v1`, when the terminal stops
building its own previews.
## The terminal's transport (part F)

The terminal still executes in process. This part adds only the client it will use
once it stops: `src/ui/service_client.py`, one `ServiceClient` per attached
service. The widgets, the five mixins and the removal of the in-process fallback
are `tui-attach-v1`; nothing here imports Textual or touches a screen.

`ServiceClient.connect` asks `ensure_service` in
[part C](#the-launcher-part-c) for a live service — attaching when one answers,
launching when none does — and wraps the endpoint it gets. Every other method is
one route, one typed value and one typed failure:

| Method | Route | Replies |
| --- | --- | --- |
| `state()` | `GET /api/state` | the projection the screen paints from |
| `durable()` | `GET /api/session/durable` | transcript, queue, deliveries, recoveries |
| `send_turn(TurnRequest)` | `POST /api/turn` | `accepted`, `request_id`, stored image ids |
| `abort()` | `POST /api/abort` | whether a turn was stopped |
| `answer(id, answers)` | `POST /api/answer` | whether the question was still pending |
| `events(after=…)` | `GET /events` | an async iterator of `ServiceEvent` |

### Why `httpx` and not `urllib` in a thread

The terminal is one asyncio loop: an event stream, a render loop and a turn share
it. `urllib` and `http.client` are blocking, so every call would need
`asyncio.to_thread`, and the stream — the one request that stays open for minutes
— cannot go to a thread without a queue in front of it and a way to stop reading.
`httpx.AsyncClient` provides an awaitable request, a cancellable read and a
streaming body natively, and the parity tests already drive the real app through
its ASGI transport. It is therefore declared in `pyproject.toml`: production code
now imports it, even though it arrived transitively before.

`ensure_service` is the one call that stays blocking, because it may spawn a
process and watch for twenty seconds. `connect` runs it with `asyncio.to_thread`,
so a service that takes its time to start does not freeze the screen.

### The transport is a constructor argument

`ServiceClient(endpoint, transport=…)` takes the HTTP transport, and that is what
makes this module testable without a socket or a child process. Two double exist
for two different jobs:

- **`httpx.ASGITransport` against `create_app`** proves the wiring: the bearer
  token is accepted, the state and the durable read come back as the projections
  `views` defines, a turn carries its images in order, and each refusal arrives as
  its named failure.
- **A scripted transport** covers what the ASGI transport cannot. It buffers a
  response and asserts it completed, so a stream that never ends cannot pass
  through it. The scripted one hands over bytes a real server would produce only
  by failing: an event cut before its blank line, a body that quotes the token.

### The token is a header, and it is scrubbed

The token is read from the endpoint file, put once into the client's default
headers, and never written anywhere else. It is not in the base URL, so it cannot
reach a proxy log through a request line. It is not in a message and not in
`repr`: a client is printed in a debug report, and a report must not carry a
credential. As a second barrier, every message the service sends is passed through
the token before it becomes an exception — a service that echoes the credential in
an error body becomes a redacted line in the terminal, not a leak.

### The SSE parser is a state machine

`SseDecoder` reads bytes and returns frames; it knows nothing about HTTP. A read
can end in the middle of a field, of a line, of an event, or of a multi-byte
character, so it accumulates, consumes whole lines and dispatches only on the
blank line. It joins several `data:` lines with `\n`, ignores comment lines —
including the service's `: keep-alive` heartbeat — and reads `\n`, `\r\n` and
`\r`. An event cut before its blank line is held and never emitted: an incomplete
frame is a broken event, not a short one.

### One connection per stream, and the cursor belongs to the caller

`events(after=…)` opens one connection and sends the cursor as both
`Last-Event-ID` and `?after=`, because a browser resends the header by itself
while a hand-rolled client passes the query, and the route reads either. Closing
the iterator is a clean cut: the connection is released and nothing is raised.

A stream the service ends on its own is `StreamInterrupted`, carrying
`last_event_id`. Reconnecting is `events(after=that id)`, and the service replays
what was missed — the reason every event carries a sequence number. The client
does not reconnect by itself: that needs a delay between attempts, and a retry
policy belongs to the screen that can also tell the person what is happening.

### The typed failures

| Failure | `code` | When | What the screen does |
| --- | --- | --- | --- |
| `ServiceUnavailable` | `service_unavailable` | nothing answered, or the launcher gave up | retry; `launch_code` says `service_starting` or `service_died` |
| `Unauthorized` | `unauthorized` | `401`: the token is not this run's | attach again; this run is gone |
| `ServiceBusy` | `busy` | `409`: the session is in the way | wait for the turn, then repeat |
| `RequestRejected` | `rejected` | `400`: the request names something unusable | fix the message |
| `PayloadTooLarge` | `too_large` | `413`: the body passed the command ceiling | fewer or smaller images |
| `UnexpectedResponse` | `unexpected_response` | a status or body this client cannot name | report it; the service and the client disagree |
| `StreamInterrupted` | `stream_interrupted` | the stream ended without being closed | `events(after=last_event_id)` |

No `httpx` exception reaches the caller, and no failure is swallowed: a status the
client does not know is `UnexpectedResponse` with the status in the message, not a
silent `None`.

### What is not decided here

- **The widgets.** Streaming deltas into the transcript, questions, images and
  rollback stay where they are; `tui-attach-v1` replaces their source, not their
  behaviour.
- **Retrying.** `TurnRequest` generates the `request_id` on the client and keeps
  it, so retrying the same instance is the correct retry and a new instance is a
  new message. How many times to retry, and when, is the caller's.

The question deadline used to be on this list. It is not any more: a question now
has a bound and a rule for what the user sees when it passes, documented under
[the question deadline](#the-question-deadline).

The tests live in `tests/test_ui_service_client.py`: unit tests for the protocol,
ASGI contract tests for the routes, and scripted-transport tests for the stream.
None of them sleeps, and none needs a port.

## The terminal attached to the service (part G)

The terminal no longer executes anything. `src/sigil.py` — the only production
entry point — asks [part C](#the-launcher-part-c) for a live service, reads the
state it projects, and builds the window over a `ServiceClient`. There is one
executor per session: the service.

This part is deliberately narrow. It moves **the conversation** onto the client and
leaves every other panel where it is, so each remaining command can move with its
own cut instead of half-moving all of them at once.

### What moved, and what did not

| Area | Now | Consequence |
| --- | --- | --- |
| Sending a message | `POST /api/turn` through `TurnMixin.submit_to_service` | the composer stays usable; the service decides turn or steer |
| Streaming | `GET /events`, applied by `SigilApp.apply_service_event` | deltas, tool calls and results, turn boundaries, notices |
| Steering | the same send, with the queued line marked until `steer_delivered` | the run reads it at its next seam, as before |
| Stopping | `POST /api/abort`, after the same double `Esc` | the service kills the shells and cancels the run |
| Leaving | nothing is sent on the way out: `/quit` and the window key close the stream and detach | the service keeps running, another terminal's turn is untouched, and [idle shutdown](#leaving-a-terminal-detaches-the-service-keeps-the-work) is what ends it |
| Questions | the same `QuestionPanel`, answered with `POST /api/answer` | the panel's keys and layout are unchanged; a question counts as answered only once the service has taken the answer, and a failed send is offered again (see [the question deadline](#the-question-deadline)) |
| Images | ordered `OutgoingImage`s with the `[Image #n]` marker | the picture that was attached is the one stored |
| Reconnection | `events(after=last_event_id)`, then a state read | a gap is announced, never silently painted over |
| Restart recovery | `ServiceClient.refresh_endpoint()` before the retry, then a fresh `/api/state` | the client re-discovers through the injected launcher, rebuilds its pool over the transport it was given, and restarts the cursor with the new run (see [When the service restarts](#when-the-service-restarts)) |
| Commands | `handle_command`, through `src/ui/command_transport.py` | client commands run here; core commands name the route that serves them and what is still missing (see [Which commands run here](#which-commands-run-here)) |
| Provider, agent, rollback | unchanged, still local | those panels read files and rows this process can see |
| The agent tree | `state.tree`, `POST /api/tree/stop`, `POST /api/tree/background`, `GET /api/tree/node/{id}` | read and driven by the service; see [part H](#the-agent-tree-through-the-service-part-h) |

The dispatcher is not replaced any more. An earlier version swapped `handle_command`
for a single refusal, which was honest about the missing senders and wrong about
everything else: it refused `/theme` along with `/rollback`. The decision now comes
from one table, read once, at the choke point every path goes through.

### The question deadline

A question is the one place the service waits on a person, and the wait is bounded.
`src/server/runtime.py` holds the deadline as `QUESTION_TIMEOUT_SECONDS` (900 s, the
same "someone will come back" as the idle window); `SIGIL_QUESTION_TIMEOUT` overrides
it, and the `question_timeout=` argument of `ServerRuntime` is the seam tests use. A
value that is not a positive number of seconds raises, naming the variable: a typo
must not quietly leave the default in place.

When the deadline passes, nothing is answered on the user's behalf. `_ask` resolves
the waiting future with `None` — the value a cancellation already means — so
`ask_user` takes the path it has always taken: it cancels the run, the turn ends where
it stood, and `turn_finished` reports `cancelled`. The question itself is published as
closed, and the person who comes back to the screen is told why:

- `question_closed` carries `id` and `reason`. The reason is `answered` or `cancelled`
  when a client resolved it, `timeout` when the deadline did, `closed` when the
  process left with the question still open, and `withdrawn` when the turn stopped
  first. A client that only needs the panel to stop asking reads `id`; the reason is
  there for the one that wants to say what happened.
- A `notice` with tone `warn` says the question went unanswered, that the turn
  stopped, and that sending the message again continues. Without it the panel would
  simply vanish, which reads as a bug rather than as a deadline.

`ServerRuntime.close()` releases an open question with `reason='closed'` before
anything else. `abort` and the turn's `finally` already did it on their paths; a
graceful stop or a failing start did not, and the future left behind had a task
awaiting it that no longer had a client.

The terminal's half is a marking rule, and it lives in
`SigilApp.answer_service_question`: a question is recorded in `_service_answered`
**only after** `POST /api/answer` succeeds. A send that fails — a service that
restarted, a connection that moved — shows `failure_notice(exc)` and returns without
marking, and the claim on the panel is released in the `finally` so the next
`/api/state`, or the reconnection, offers the same question again. Marking it on the
way out instead would leave the service waiting out its deadline while the screen
showed a prompt with nothing left to answer.

Identity is the id (`q1`, `q2`, …), never the text: two questions asking the same
words are two questions, and both can be open in one session's lifetime.

### Which commands run here

The catalogue decides **who may serve a command**. The transport table decides
**whether this terminal can reach that server**, and the two are different questions:
the first is shared vocabulary, the second changes with each cut.

`src/ui/command_transport.py` holds one row per command. A row carries the name, the
owner the catalogue already declared, the route the service serves it at, and exactly
one state:

| State | Meaning | What the terminal does |
| --- | --- | --- |
| `client` | this window does the work | runs the handler here; sends nothing |
| `delegated` | the service does the work and a sender exists | calls the route |
| `not_wired` | the service does the work and has the route; no sender here yet | one notice: the command, the route, and what is missing |
| `blocked` | the service has no endpoint for it | one notice: the command and why there is nothing to call |

Eight commands are `client`: `/theme`, `/sidebar`, `/debug`, `/alerts`, `/tools`,
`/reasoning-visibility`, `/help` and `/quit`. They repaint this window, so they never
travel, and an attached terminal runs them exactly as one that owns the engine does.

Nine core commands are `delegated`: `/context`, `/external-files`, `/mcp`, `/effort`,
`/agent`, `/new`, `/load`, `/model` and `/rollback`. Their handlers send to the service and paint the answer
it returns, and none of them writes the session locally — the row it changed belongs
to the service, and a second copy here would be a second truth about one
conversation. The pickers stay in the terminal; only the list and the effect travel.

`/agent` is the clearest case of that rule. The switch is `POST /api/agent`, and what
lands on screen afterwards is the service's `state.session.agent` — the terminal no
longer stamps `session.agent` itself, because the profile a session runs under is a
fact about the session and not about the window looking at it. For the same reason a
second attached terminal shows the new agent after it refreshes, without either one
telling the other anything.

`/new` and `/load` repaint the transcript and restart the event cursor from the state
they just read, instead of reusing what the terminal had: the cursor is a position in
the running service's sequence, and an old number would silently skip everything the
new session publishes. Opening a session can also move the service to another
workspace; the header follows the projection, not a local guess.

One core command is still `blocked`: `/default-models`. The screen edits the two config files
that decide what a profile starts on, and those files belong to the service — a terminal
that wrote them would be a second writer on one configuration, which is the failure the
attached terminal exists to remove. The service serves both routes now (see
[the defaults the terminal could not edit](#the-defaults-the-terminal-could-not-edit)), so
what the row is missing is a sender and not an endpoint: it stays `blocked` until
`model-defaults-client-v1` points one at `GET`/`POST /api/model-defaults`, and until then
the dispatcher answers it at the door with `unavailable_notice`. That sentence — *"a
service command, and the service has no endpoint for it yet"* — is one cut behind the
service, and `show_default_models` is still never reached attached: its screen and its
collaborators belong to a terminal that owns the engine, so that handler has nothing to
say about a mode it cannot run in.

That row is the single exception, and `test_only_the_documented_gap_is_blocked` pins the
blocked state to exactly it. `not_wired` stays empty: it would promise a sender that is not
coming in this cut. The decision has to be total — a command whose row says it cannot run
here must say so precisely rather than borrow another answer. Unblocking the row is the
whole of `model-defaults-client-v1`, and it is the only change it makes to this table.

### The defaults the terminal could not edit

`/default-models` was the one command with nothing to call: the two files that decide what
a profile starts on belonged to the service, and no route projected them. The service owns
them again, so it serves them; pointing the terminal's sender at these two routes is the
next cut.

| Route | Does | Reply |
| --- | --- | --- |
| `GET /api/model-defaults` | Both config files, per file, and the accounts their entries name | `views.model_defaults_view` |
| `POST /api/model-defaults` | Sets or clears one entry in the file the destination names | the same projection, after the write |

The read is per file and never merged. `files` carries each destination's path, whether
it exists, the `default_model` block, the `agents` map, the entries the reader rejected,
and the sentence that says why; a file that is not there yet projects as an empty one,
which is exactly what the reader answers for it. `targets` carries the merge instead: one
row per target — `null` for the block a conversation starts on, then every agent either
file mentions — with the entry that wins, the files that mention it, and whether that
entry is the one the reader could not parse. The precedence stays in
`ModelDefaultsState`, so no client holds a second copy of it. `accounts` names the aliases
the entries use and whether the router has each one, which is what lets a screen say
*"account `work` is not configured"* instead of printing an alias nobody can place.

`POST` is the engine's (`Composition.save_model_default`), and so are its refusals. A
config file that cannot be read is not rewritten, and the answer carries the engine's own
sentence. A turn in the way answers `409`, which is the rule the terminal's
`/default-models` already applies (`BUSY_COMMANDS`): a subagent reads `agents.<name>` the
moment it is summoned, and the subagent criterion belongs to `/mcp`, so it is not added
here. `400` is for a request that names something unusable — a destination that is not
`project` or `global`, an agent name the config cannot hold, a level `parse_choice` does
not know — because a different request would work. Clearing an entry is `{"config": null}`
and has to be spelled out, so a field a client forgot cannot delete one. The reply is the
state the write left, re-read from disk, so a client repaints what is there rather than
what it asked for.

The files stay the service's. Both directions go through the composition that owns them —
the same reader and the same writer the in-process terminal uses — which is what keeps one
configuration from having two authors.

### The help is derived, never repeated

Every listing of commands reads the same two tables: `src/engine/commands.py` for a
command's name and sentence, and `src/ui/command_transport.py` for whether this terminal
can act on it. `src/ui/help_text.py` renders a row as the catalogue's words plus
`listing_mark`, and `/help` and the `/` menu both call it — the help with
`attached=self._client is not None`, the menu with the flag its mixin passes down.

Nothing else may state a command's availability in its own words. A second copy of "why
this cannot run here" is a second thing to keep in step with the table, and the copy that
drifts is always the one the user reads. So the mark is three words the table justifies,
the sentence a refused command prints is `unavailable_notice` and only that, and a handler
in a mode it cannot be reached in says nothing at all rather than answering for it.

`/provider` was the last one, and it needed a route rather than a sentence.
`GET /api/accounts` lists the accounts with `has_credential` as a boolean — the key
itself never leaves the process that stores it, and no route, log line or error message
echoes it. `POST /api/accounts` adds an API-key account and makes it active, bounded at
thirty seconds because it is the one call that reaches the provider with the user's key;
`POST /api/accounts/{alias}/activate`, `POST /api/accounts/{alias}/rename` and
`DELETE /api/accounts/{alias}` complete the set, and all four answer `409` while a turn
runs, before any slow work starts.

Two rules are worth naming because they are refusals, not conveniences. **Deleting is
refused for the active account**: the engine would promote a successor, but only a person
at the terminal sees the model change under their feet, so the route insists on
activating another account first — and with a single account that is also the answer,
since it is the active one. **Browser sign-in is not offered over HTTP**: the OAuth flow
needs a browser and the process that owns it, so an OAuth request is refused with that
reason and the account has to be added from the terminal running the engine. That is a
real limitation, not a missing wire.

`/model` needed one route the service did not have: `GET /api/models` lists the
accounts, not the models of each one. `GET /api/models/{alias}` answers that list from
`ModelService.catalog_for` — the same call the in-process terminal makes — and bounds the
wait at fifteen seconds so a provider that never answers becomes a refusal with a
sentence instead of a request that stays open. When the provider cannot be read, the
route refuses with the engine's own reason: the fallback list the in-process terminal
shows next to a warning is deliberately **not** offered here, because a picker listing
options the provider did not send is exactly the invented answer a delegated client must
not produce. The picker itself stays in the terminal, built from that projection.

`/rollback` needed the prompt of the turn it drops. The rows that hold it are truncated
by the rollback, so the route reads it first — `prompt_of_turn` — and carries the text in
the `rollback` reply. That is the field the in-process terminal used to look up before
applying; projecting it changes no count and no row, and it means no client needs a
database of its own. Images travel with it: `prompt_images` carries each picture as
base64 plus its media type and identifier, so the composer restores the message with the
picture its `[Image #1]` marker names. The service re-encodes an image when it stores it,
so a client receives the stored bytes and not the sent ones; the pixels are what match.
The fork reply already carried its session id, so `/fork` needed nothing on the server
side.

**Still uncovered.** Three gaps this cut leaves open:

- **OAuth over HTTP.** Browser sign-in needs the process that owns the flow, so the API refuses an OAuth request with that reason instead of faking a key.
- **`SecretRejectedError`.** The client translates the class, and no test drives that path.
- **An empty catalogue on `activate`.** Activating an account whose provider returns no models has no test.

A command that cannot act answers in one notice, opens no panel and writes no queue
row. An unknown command keeps its own answer, because a name the catalogue never
declared is not a transport decision in the first place.

The order at the choke point is the decision, in this order: a `client` row runs
here, a `delegated` row runs its handler (which sends), and a `not_wired` or
`blocked` row answers once and returns.

A delegated command whose action would change the session is refused **before** it
opens anything, while a turn runs. The terminal reads `busy` from `/api/state` — the
same fact the service would use — so `/agent`, `/model`, `/effort`, `/new`, `/load`,
`/mcp` and `/external-files` answer with one line instead of opening a picker, fetching
a catalogue and only then being told the session is in the way. The sentence is the
service's own ("a turn is running: finish or stop it before you …"), so there is one
wording for one fact rather than two that drift apart.

Not every `409` is that fact, though. `/api/session/open` and `/api/rollback` also use
it for a turn this session cannot reach or a session that does not exist, so
`failure_notice` reads the service's sentence: when it is not about a running turn, the
notice repeats what the service said instead of claiming the agent is busy. A rollback
that could not restore files says so in the same words the in-process terminal uses,
because both call one `_files_report`.

The table is imported against the catalogue and fails at import when a row is missing
or extra, so a new command cannot become a menu entry that silently does nothing. Two
tests hold the same line: `tests/test_commands_ownership.py` for the table and
`tests/test_ui_client.py` for the behaviour behind it.

The three side doors — `ctrl+m`, `ctrl+l` and `ctrl+a` — call `handle_command` instead
of `open_models`, `open_effort` and `open_agent`. A key is another door into the same
room, and a door with a path of its own is a door that answers differently. Because
`/effort` is delegated, `ctrl+l` opens the picker fed by the service rather than
answering a refusal; the levels it offers are the ones `model_view.efforts` reports
for the model the service is actually running.

### The first connection starts after the state, not at zero

`/events` keeps a buffer for reconnections. A terminal that opened the stream at
sequence zero would be handed everything still in it — a transcript the state it
just read already describes — and paint it twice. So the first connection starts at
`state['seq']`, and only a reconnection uses the last event applied. The two cursors
answer different questions: *what have I already got* versus *what did I miss*.

### When the service restarts

The address is fixed when the client is built — every other request depends on that
being a fact rather than a guess — so a restart cannot be absorbed silently. The
follow loop is where it is handled: when nothing answers (`ServiceUnavailable`), it
asks `ServiceClient.refresh_endpoint()` before dialling the same dead address again.

That call re-discovers the service through the launcher the client was constructed
with, compares the result with the endpoint it holds, and rebuilds only when host,
port or token differ. Rebuilding is two steps: `_aim` takes the new address and
credential, and `_build` makes a new pool over **the transport the client was
given** — a rebuild that dropped it would dial the network from a test that handed
the client a double — after which the old pool is closed.

The cursor is the part that is easy to get wrong. Sequence numbers are **per run**:
a restarted service numbers from one again. Reusing the cursor this screen had
against the old run would open the new stream *after* a number the new service has
not reached, and the terminal would follow a live conversation while silently
skipping everything published before it caught up. So the recovery resets the cursor
to zero, reads `/api/state` from the new service and takes the cursor from that
answer — the same rule the first connection follows — then repaints the transcript
from the new run's durable rows.

The answered questions go with the old run for the same reason. Question ids are
per run: a service numbers them `q1`, `q2`, … from one on every start. The recovery
clears `_service_answered`, because an id kept from the previous run suppresses a
brand-new question that happens to reuse a number the old run already resolved — and
the service then waits out its deadline for an answer no screen will ever offer.

In practice the screen ends up following whatever the new run has, which is not
necessarily the conversation it was showing: the old conversation is durable, but a
restarted service opens its own session and reattaching the terminal to the previous
one is not part of this cut.

### What is painted when the terminal attaches

The stream continues a conversation; it is not one. So attaching paints the
conversation the service already has, from `/api/session/durable` — one coherent read
that carries the projected transcript and the queue together:

| Painted | From | Detail |
| --- | --- | --- |
| A user line | `transcript` items of kind `user` | the text as it was typed, `[Image #n]` markers included, plus the description of each image (`1 image (640×480 PNG)`) |
| An assistant block | `assistant` and `thinking` items | the answer and its reasoning, grouped per response |
| A tool call | `tool_call`, then `tool_result` | name, arguments and the result the service presented, paired by `call_id`; a call with no result stays marked interrupted |
| A waiting message | `pending` | painted with the queued mark, and retired when the service confirms delivery |

What is **not** painted, and why:

- **Timing on stored calls.** The projection carries no duration for a call that
  already finished, so a stored tool row shows its outcome and not `120 ms` — and a
  repainted conversation loses it for the same reason. Inventing one would be a number
  nobody measured.
- **A subagent's transcript.** The tree is a later cut; the durable read of the root
  execution does not carry another execution's messages.
- **Messages that exist only in a recovery copy.** They are listed as recoveries and
  never merged into the transcript: merging two conversations would misrepresent both.

The queue is painted as *waiting* rather than as history for the same reason the state
exists at all: a message the run has not read is not part of the conversation yet. The
confirmation event carries the service's own enqueue id, not the request id the
terminal generated, so waiting lines are retired in queue order — the oldest one is the
one the run read. A line painted from the durable read is removed on confirmation
(it now belongs to the transcript); a line typed in this window only loses its mark,
which is what a live turn has always done.

A gap during a live turn **discards the block in progress**. The transcript is rebuilt
from the rows and the deltas that follow open a new block, because an unknown number of
deltas never arrived: a conversation with a hole in it reads better for a second and
lies for as long as it is on screen.

### The chrome comes from the state too

An attached terminal composes no `WorkspaceManager`: the workspace is the service's.
So the other half of the window title and the status bar's project and worktree are
read from `state['workspace']`, the same projection `views.workspace_view` already
builds for the web header:

| Painted | From `state['workspace']` | Rule |
| --- | --- | --- |
| Window title, repository half | `repository` and `is_worktree` | `sigil` for a checkout, `sigil:better-debug` for a worktree — the label is what tells two trees of one repository apart |
| Window title, folder fallback | `root` | outside git there is no repository name, so the folder is what is left |
| Status bar, project | `root` | the directory name, exactly as the in-process bar shows it |
| Status bar, worktree badge | `label`, `branch`, `is_worktree` | the same `WorkspaceBadge` the manager produces |
| Diff | `root` | polled off-thread against the service's root, not a local path |

Before this, both halves asked a manager that an attached terminal does not have, and
painted nothing while the answer sat in the state the client had just read. A
projection that has not arrived yet paints nothing — an empty title is honest, a
locally guessed one is not, and the state is the only process that knows where the
conversation runs.

### The image capability is the projection's answer

`ModelSnapshot.images` defaults to `True`, which is harmless inside the process that
owns the model and wrong the moment it crosses a boundary as a fact. So
`views.model_view` projects `images: null` while no model is configured — `configured`
is the field that says whether the rest of the model projection means anything — and a
real boolean only when a model is known.

The terminal's `accepts_images()` follows that: attached, it reads `state['model']`
and answers `None` for *unknown*, never `True` by default. The composer refuses the
image either way; what changes is the sentence the user reads. It says the capability
cannot be known and to pick a model with `/model`, instead of accepting the picture and
letting the provider refuse it after a whole turn was spent. This is a wire contract:
`null` now means *unknown*, and a client that reads the field as a boolean has to handle
it.

### Leaving a terminal detaches: the service keeps the work

The service outlives a terminal by design: closing the window is not a reason to
interrupt work, and the service stops itself when that work ends (see
[idle shutdown](#idle-shutdown)). Leaving is that and nothing more — `/quit` and the
window's own quit key both close this client's stream and detach it. **No exit path
sends anything.** Sending a stop from the exit was the bug: `abort` is global to the
service, so closing one terminal cancelled the turn a user had started from another
one, and because the session is saved at cancellation, the message only then appeared
in the transcript.

`POST /api/stop-all` still exists and is unchanged — `ServerRuntime.stop_all()` calls
`abort()` (shells killed by process group, the run cancelled, open queue rows stopped)
and then `summoner.cancel_all()`, which is the half `abort` cannot cover: with no turn
running a background subagent would otherwise keep spending. Nothing in the route kills
the service's process. What changed is the caller: no client calls it on the way out.
Stopping the whole session is a deliberate act, never a side effect of closing a window,
so the route is there for an explicit command rather than for an exit.

The one thing that ends the service is therefore the idle window: when no row is open
and no turn runs for `DEFAULT_IDLE_GRACE` (900 s), the watch asks for a graceful stop.
A closed terminal is not part of that decision — it is not work, so it neither keeps the
service alive nor ends it.

The attached terminal's queue overlay is the other half of the same contract, and it is
painted per row rather than per turn. The service announces an acceptance with
`state_changed` and `reason='queue'`, carrying the fact and not the text; the screen
that did not type the message re-reads `/api/session/durable` and paints the waiting
lines from `pending`. Only the overlay is repainted — the transcript is rebuilt from the
rows when the stream stops — because clearing the chat mid-turn would drop the deltas
streaming into the block being written. That is what used to keep another terminal's
message invisible for the whole turn while its screen only said "Thinking".



A turn that ends with something to report asks for attention. The request is held until
the squad is quiet, and the squad is the service's tree, not this process's:
`settle_attention` stores the event, and `subagents_running` counts the node statuses of
the state projection.

Counting locally would report zero the moment the turn closed, because a subagent
outlives the turn that summoned it. The notification would fire while the work still
ran and call the user back to a screen where nothing was ready — the early ring this
waiting exists to prevent.

The summoner remains the fallback for the in-process seam, which has no projection to
read. Attached, the client never touches it.

### What the terminal still holds locally

Three things, and none of them can execute a turn:

- **Preferences and themes** are this client's own: they describe the window, not
  the conversation.
- **The agent profiles and the roadmap** are files and rows it reads exactly as the
  service does, for the panels that have not moved. `src/sigil.py` still builds a
  `RoadmapStores` for the attached terminal, because other panels and commands keep
  using it, but the sidebar no longer reads it: the plan on screen is the service's
  projection. The reason is below, under *Where the sidebar reads each section*.

The model service is **not** among them. Building it is building an engine, so no
part of the attached terminal constructs one.

### Where the sidebar reads each section

Attached, the sidebar paints the service's projection and measures nothing itself.
`refresh_sidebar` branches on `self._client is None`: the in-process path below is
untouched, and the attached path reads `_service_state` plus one meter route.

| Section | Source in attached mode | What is *not* done |
| --- | --- | --- |
| Model | `state['model']`: `account.alias`, `account.kind`, `model`, `thinking` | no `ModelService` is built; the route does not project an auth label, so the panel does not show one |
| Context and cost | `GET /api/context` through `ServiceClient.context()`, after `_apply_service_state` | no local `ContextMeter`; the terminal's history is empty by construction |
| Loaded skills | `state['skills']`, derived by the service from its own transcript | no `viewed_skills()` over a transcript this process does not have |
| MCP | `state['mcp']['servers']`, keeping `enabled` entries with `ok = not error` | no probe: connecting here would open a second set of subprocesses |
| Roadmap | `state['roadmap']`, the root execution's plan | no `RoadmapStore` |

Two rules decide what a gap looks like:

- **Absent is unknown, never zero.** A key the projection does not carry is painted
  `unknown — the service did not report it`. `none yet`, `No tasks yet.`, `not
  configured` and `0%` are claims about a session; a missing key supports none of
  them.
- **A zero that the service sent is real.** A cold session with a known window
  answers `percent: 0.0`, and the panel paints `0%`. `mcp.loading: true` is the
  exception on the other side: the layer has not answered yet, so it is treated as
  not reported rather than as an empty list.

The plan is read from the projection instead of the terminal's own
`RoadmapStores` for one reason. `RoadmapStore` loads its rows once, in its
constructor (`src/engine/roadmap/store.py`), and only `reload()` or `bind()`
refreshes them; `RoadmapStores.for_node` caches the store per node. The service
writes the plan through its own store (`src/engine/bootstrap.py`) and projects it in
`/api/state`, and nothing in the attached terminal subscribes to those writes. The
local copy is therefore a photograph taken at mount: showing it would be showing
frozen rows without saying so. The service's copy is the only one that moves.

Repainting needs no timer. `apply_service_event` already routes `state_changed` to
`refresh_service_state`, which applies the state and calls `refresh_sidebar`, and
`on_mount` takes the same path through its first `refresh_service_state`. The
context line is the one asynchronous read, on an exclusive worker
(`sidebar-context`), so a newer measurement replaces an older one instead of
queueing behind it.

The cost mark this mode cannot reproduce is `+`. `/api/context` projects `total`,
`approximate` and `requests`, but not the `unpriced`/`unmeasured` counts that make a
total a floor; the panel shows the amount without inventing the mark. Widening the
route is the fix if that mark is wanted, not guessing it here.

### What this process measures, and what only the service does

An attached terminal holds no history, no meter and no run. Every number it paints
comes from the service's projection or is named as unmeasured: a local zero is a
measurement of a conversation this process has never seen, and `model: ?` next to a
model the service has selected is the same mistake with a question mark.

| Surface | Attached source | What replaces the local value |
| --- | --- | --- |
| `alt+d` view and `/debug --text` | `GET /api/context`, `state['model']`, `state['session']` | `not measured in this process — the service owns the turn` |
| Position (`seq`) of a typed message | the service's rows, re-read after it accepts the message | no position: the line is not clickable until a row exists |
| Sidebar subject line | the session | `session · <agent>`, never the open node |
| Turn clock | this terminal's own interval while the turn runs | — the elapsed time is real, because the turn started when the service said so |

**The debug view.** `DebugMixin` keeps its in-process path untouched: there the
report is measured from `_history`, `_meter` and the captured request of the run
this process is executing. Attached, `debug_report` is not used at all.
`ServiceDebugReport.from_projection` maps `/api/context` and `/api/state` onto what
the report prints, and `service_debug_lines` is the single list of lines both
`/debug --text` and the widget paint, so the two cannot disagree.

The view repaints on the service's own events — `turn_started`, `tool_result`,
`state_changed` — because there is no run here to react to. `refresh_service_debug`
coalesces a burst of them into one read on an exclusive worker (`debug-service`). A
route that does not answer is reported as `the service did not answer /api/context`,
never as a session at zero.

The per-request measurements — cache reads and writes, compaction decisions,
instruction and tool-schema sizes, turn timings — happen inside the turn, which the
service owns and does not publish. They are listed under *What this process does not
measure*, not reconstructed from an empty meter. Widening `/api/context` is the fix
if one of them is wanted.

**`seq` of a typed message.** In process the position is `len(_history)`: the prompt
is the next message, and knowing it up front is what makes the line clickable before
the turn ends. Attached, `_history` is empty by construction, so that number was
always `0` — and `0` is a position, the first rollback point, which preselected the
first turn on every click. `on_prompt_input_submitted` passes `None` there instead
and the line stays inert (`UserMessage.on_click` returns early) until the transcript
is re-read from `/api/session/durable`, where each row's index is its real `seq` and
the click works again.

**The sidebar's subject.** The service projects the model, the context budget, the
loaded skills and the plan of the *session*; it projects none of them per node.
`SidebarPanel.update_subject` names the session — `session · <agent>` — while a
subagent is open in an attached terminal, and the node on screen is named by the node
view and by the window title. In process the label still names the subagent, because
there the panel does read that node's own history, meter and plan.

**The turn clock.** In process `run_turn` schedules a one-second interval that
repaints the elapsed time and stops it when the turn ends. Attached there is no run,
so nothing scheduled it and the spinner sat on `0s` for the whole turn.
`ActivityMixin.set_activity` now schedules `activity_tick` itself while `_client` is
set, and drops the interval when the turn stops. The in-process path keeps the one
`run_turn` owns, so the same second is never painted twice.

The tests for these four rules are in `tests/test_debug_view.py` (the report as a
pure function) and `tests/test_ui_client.py` (the repaint on an event, the cleared
answered set, the subject line, the missing position, the clock).

### The test seam, and why it is allowed to exist

`SigilApp` still accepts in-process collaborators — `agent=`, `store=`, `session=`.
That is a seam, not a fallback, and it is fenced three ways:

1. `client=` and `agent=`/`store=` are mutually exclusive; passing both raises.
2. `src/sigil.py` passes a client and nothing else, and imports no `Composition`.
   The production path has no agent to hand over even by mistake.
3. The seam is named as such in the constructor's docstring, so the next reader
   meets the decision instead of rediscovering it.

`TurnMixin.run_turn` — the in-process turn loop, with its checkpoints, steering and
abort — still exists behind that seam, because the ~40 widget tests that mount
`SigilApp` assert on it directly. It is a real duplication of the turn loop that
`src/server/runtime.py` already owns, and it is scheduled for deletion: when those
tests mount a client (a fixture that starts a runtime, like
`tests/test_ui_client.py`), `run_turn` goes with them. Until then, nothing may grow
in it that both paths need — the moment a decision is written twice is the moment
the two disagree.

### What this cut does not cover

Three gaps, each stated as it is:

- **A rebuilt gap is a repaint, not a splice.** `report_stream_gap` announces the gap,
  re-reads the state and repaints the transcript from the durable rows. The events
  that never arrived are not recovered one by one, and the block being written is
  discarded on purpose: an unknown number of deltas are missing, so continuing that
  block would draw a sentence nobody said.
- **A stored tool call keeps exactly what the projection carries.** That is its name,
  its `call_id`, its `args` and the result the service presented, so a stored call and
  a live one paint the same. The exception is a *builtin* call: `views._part_view`
  projects its name and its result text but no arguments, so a stored web search is
  painted without them.
- **No `duration_ms` for a stored call.** The absence is described under *Timing on
  stored calls* above, and it is the same one a restarted or reconnected screen
  inherits: `tool_finished` carries the measurement, nothing else does.

### The tests

`tests/test_ui_client.py` proves the wiring against the real application: real
`create_app`, real `ServerRuntime`, real routes, real hub. The one substitution is
`/events`, and it is explained where it happens: `httpx.ASGITransport` collects a
response body and returns when the application finishes, so a stream that stays
open for the life of a terminal cannot go through it. `_ServiceTransport` therefore
splits by route — every request through ASGI, `/events` served from the runtime's
own `EventHub` with `Event.to_sse()` and `replay(after)` — so the bytes, the cursor
and the sequence numbers are the service's while the read side stays cuttable.

Covered: sending to the durable queue, streaming into the transcript, steering,
stopping with the two-press rule, a question answered over HTTP, an image with its
marker, the first cursor, and a cut stream that announces the gap, resumes and
re-reads the state. Nothing waits on a fixed sleep; every wait is for a row, a
rendered line or a request count.

## The agent tree through the service (part H)

The tree window reads and drives the service's tree. Before this cut it read
`SigilApp._summoner` and `_agent_tree`, and both are `None` on an attached
terminal: `ctrl+t` opened an empty list and every action on a row did nothing.

### Where the nodes come from

There is one source of truth per mode, and the mode is one attribute:

| Mode | Nodes from | When |
| --- | --- | --- |
| Attached | `state['tree']['nodes']` of the last `/api/state` | `_client is not None` |
| In process | `_agent_tree.active.nodes()` | the test seam only |

`TreeMixin.tree_nodes()` picks by `_client`, and `open_tree` re-reads the state
before pushing the screen, so the window is the truth as of the keypress instead
of the last event that moved a node. A second source of truth would be a list
that disagrees with the agent it describes.

The projection is JSON; the screen paints `AgentNode`. `tree_mixin.nodes_from_tree`
and `node_from_view` are the one place the two meet, and the widget is untouched:
ids, tree position, state, mode, task, runs and budget travel, and `elapsed` is
turned back into a clock — a finished node's clock stops where the service
stopped counting, a running one keeps going. An entry that does not read back is
dropped, the same rule a stored tree keeps.

### `ctrl+t` always opens

The window opens with one subagent, with forty, and with none. The empty case is
drawn by the list itself: the status line says `No subagents in this session
yet.` and the row `Subagents appear here while the agent works.` The old
`NO_TREE_NOTICE` — "this session has no agents yet" — now belongs only to an
in-process app built without a tree; an attached terminal never shows it, because
the session does have an agent and the service is the one that knows.

### Actions are the service's

| Key | Route | What changes |
| --- | --- | --- |
| `enter` | `GET /api/tree/node/{id}?include_messages=true` | the node view, painted from the service's transcript |
| `k` | `POST /api/tree/stop` | the node and everything under it, in the service |
| `b` | `POST /api/tree/background` | `mode` becomes `async`, in the service |

`ServiceClient.tree_node`, `stop_node` and `background_node` are the three calls.
They carry the client's existing failure vocabulary: `400` is `RequestRejected`
(a ref that names nothing), `409` is `ServiceBusy` (the root, or a node that
already finished) with the engine's own sentence, and `failure_notice` prints it.
`include_messages` is **advisory**: the route always returns `transcript`, and the
flag documents the caller's intent rather than switching the wire.

The transcript is painted through `ChatLog.render_items`, the same projection
renderer the main transcript already uses, because `AgentView` rebuilds its body
from `ModelMessage`s an attached terminal never holds. The widget keeps its type
and its header; only the body's source differs.

### What is left of the local path

It is the in-process seam and nothing else. `TreeMixin` keeps the summoner calls
(`summoner.kill`, `summoner.detach`, `summoner.resolve`) behind `_client is None`,
which is the same fence part G describes for `run_turn`: those paths serve the
widget tests that mount `SigilApp` with an engine. No production entry point can
reach them, and nothing new may grow in them that both modes need.

### Keeping the list current

`tree_changed` is a state change, so it lands in `_apply_service_state`, which now
calls `_refresh_tree_screen()`: an open tree repaints from the freshly read
projection. On an attached terminal `on_tree_changed` does not read the node out of
the event — that node is not one this process holds — it refreshes the state and,
when a subagent is open, re-reads that node with `include_messages`. No timer and
no fixed sleep: the repaint is driven by the event and by the state it asks for.

### The tests

`tests/test_ui_service_client.py` proves the three methods against the real
application (happy path, `400` for an unknown node, `409` for the root and for a
finished node) and `tests/test_ui_client.py` proves the window: empty tree opens
and says so, a service subagent appears in the list, `k` and `b` change the node in
the service and the list shows it, `enter` paints the transcript the service
keeps, and a published `tree_changed` repaints an open list. Every wait is for a
rendered row or a service fact, never a sleep.

### The wake, in attached mode

A background subagent outlives the turn that started it. The turn ends, the user
reads its answer, and the subagent's report arrives afterwards. The terminal
answers that with a turn of its own; the service has no timer to do it with, so
the wake is a durable input the service gives itself.

The seam is `ServerRuntime._consume`, the one place that starts turns. When the
queue has nothing to claim, the consumer asks whether a report is owed and, if one
is, accepts it as an ordinary `turn` row before claiming it. Nothing outside
`_consume` starts a turn, so a wake cannot compete with a user's message.

The decision has two facts and no clock:

| Gate | Why |
| --- | --- |
| No subagent is `running` | Three researchers that finish together are three reports and **one** turn; the question is whether the squad is quiet, not how long ago it was not |
| At least one report is pending | Nothing to read, nothing to start |

`_on_tree_idle` keeps publishing `tree_idle` and now also nudges the consumer,
clearing `_settled` so `wait()` keeps meaning "nothing left to do" while a report
is unread.

### What wakes a turn, and what never does

Only `done` and `failed` reports wake. A cancelled node is refused: it keeps
whatever it had said and keeps `delivered` false when the cancellation arrived
before anyone read it, so handing that to a turn would execute again the work
somebody stopped on purpose. A failure is the opposite case and does wake — the
parent has to see it. The rule lives in `SubagentSummoner.take_reports`, beside
`_deliver`, which decides in the same place which nodes send anything at all;
a second copy in the service would drift from it.

Three more refusals: a closing service starts nothing; a turn already running
leaves the reports for the wake that follows it; and a report an interrupted run
left behind is never re-executed — the row is durable, so `start` marks it
`interrupted` and `claim_next` does not claim it.

The prompt is the reports' envelopes followed by `WAKE_PROMPT`, word for word the
terminal's sentence, so both clients paint a woken turn as reports and not as
something the user typed.

### The tests

`tests/test_server_runtime.py` proves the wake against the real composition and
the real engine bell (`SubagentSummoner._deliver`, the last thing `_execute` does
for an async node): a report that lands after the turn starts exactly one turn and
reaches the durable transcript; two reports of one squad produce one turn with
both; a cancelled node wakes nothing and never appears in the transcript; a failed
node does wake; and a closing service stays silent. Every wait is a bounded
deadline on a service fact, never a sleep.
