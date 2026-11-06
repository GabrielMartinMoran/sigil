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
