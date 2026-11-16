# Parity commands (part D)

The terminal already had five commands that never reached HTTP: change the
model, change the effort, switch the agent, roll back, and fork. Without them a
client can watch a session and cannot drive one, so they are the last contract
the terminal needs before it becomes a client rather than an executor. This part
adds the routes and the shared decision behind them; it does not migrate the
terminal.

## One decision, two transports

The command logic does not live in a route. Each route calls a `ServerRuntime`
method, and each of those calls the same engine object the terminal already
calls:

- **Model and effort.** `ModelService.select` and `ModelService.set_thinking`.
  The service already refuses while a turn runs; `ServerRuntime` points
  `model_service.is_busy` at its own `busy` flag, so the HTTP command gets that
  `BusyError` instead of a second rule written in a route.
- **Agent.** `AgentRegistry.activate` plus `Composition.select_agent_model`.
- **Rollback and fork.** `composition.rollback`, the instance the terminal's
  picker uses.

The state a selection leaves behind is recorded once, in
`Composition.apply_model_change` and `Composition.apply_agent` — the session's
selection, the meter's window, `save_progress` when the conversation has
messages, the stamped agent, and the tree root's name. Both clients will call
those; for this cut only the server does.

The terminal's `ProviderMixin._after_model_change` and `AgentMixin.apply_agent`
still carry their own copy of those lines. That is deliberate and temporary: the
terminal is the only client in use, it is not migrated yet, and rewriting a path
this part does not exercise would be an unverified change to it. The copies go
in `tui-attach-v1`, when the terminal stops mutating state locally.

`Composition.rebuild_agent` is the server's responsibility after a model change,
because the agent closes over the model it was built with. It is not part of
`apply_model_change`: whether a client needs it depends on whether that client
holds its own agent, which is a fact about the client.

## The routes

The service routes below expose this command boundary and return typed
projections.

| Route                | Does                                                     | Reply                            |
| -------------------- | -------------------------------------------------------- | -------------------------------- |
| `POST /api/model`    | Switches account, model and optionally the level         | `views.model_view`               |
| `POST /api/effort`   | Sets the level of the current model                      | `views.model_view`               |
| `POST /api/agent`    | Activates a profile for the next turn                    | `views.agent_view` plus the list |
| `GET /api/rollback`  | Lists the turns the session can return to                | `views.rollback_points`          |
| `POST /api/rollback` | Returns to before a turn, rows and optionally files      | outcome plus the session view    |
| `POST /api/fork`     | Branches a new session at an instant, destroying nothing | `views.fork_view`                |

Every reply is a projection from `src/server/api/views.py`, and the parity
contract in `tests/server/test_server_parity.py` compares the reply to that
projection rather than to a literal, so a route cannot invent a shape the views
module does not define.

## What is refused, and with which status

The refusals are the in-process ones, carried over HTTP rather than re-decided:

- **`409`** when the session is in the way and the same request would work
  later: a turn is running (`BusyError` for model and effort, the runtime's own
  check for agent, rollback and fork), or the named turn is not a point of this
  session (`RollbackError`).
- **`400`** when the request itself names something unusable and a different
  request would be needed: an unknown account or profile, a level the model does
  not accept (`ProviderError`, `AgentNotFound`).

A refusal changes nothing. The tests assert that, reading the session, the model
snapshot or the message rows after the rejected call rather than trusting the
status code.

## Not decided here

The terminal migration, its five mixins, and removing the duplicate selection
logic are `tui-attach-v1`. The transport that cut consumes is
[part F](terminal-transport.md#the-terminals-transport-part-f). Tree actions,
subagent transcripts, the MCP toggle, external files and context are
`parity-extra-v1`, in the next section; themes stay in the terminal. Debug
rendering also stays in the terminal, but its measurements come from
`GET /api/debug`, described below.

## Parity commands (part E)

Part D gave a client the commands that change what the next turn runs on. What
it left out is what the terminal draws around the chat: the tree of agents, the
transcript of one subagent, the two session toggles, and the context meter. This
part adds those six routes. Theme selection remains client work. The debug
report now has its own read-only route, `GET /api/debug`.

## One decision, two transports

The commands are thin for the same reason as part D's: a route calls a
`ServerRuntime` method, and the method calls the object that already decides.

- **The tree.** `SubagentSummoner.detach` and `SubagentSummoner.kill`, reached
  through `Composition.background_subagent` and `Composition.stop_subagent`. The
  summoner is the object the terminal's `tree_mixin` calls, so the refusals are
  the engine's own: an unknown id, a node that already finished, and the
  session's own agent. The root rule lives in the summoner on purpose —
  `agent_tree_screen` lists every node and binds `b` and `k` on the highlighted
  row, the root included, so a guard in one caller would leave the action
  reachable from the other. `detach` and `kill` answer the root with the same
  sentence.
- **The toggles.** `Composition.apply_mcp_override` and
  `Composition.apply_external_files`, the same pair `commands_mixin` performs.
  `mcp_overrides` and `allow_external_files` are columns of the session row, so
  the change is written with `SessionStore.save_current`: a concurrent writer's
  copy is adopted instead of becoming a server error, and an empty draft writes
  nothing.
- **The context.** `Composition.meter` and `session_cost`, the two objects the
  terminal's sidebar reads. Nothing is computed in the route beyond the
  projection.

## The routes

The service routes below expose this command boundary and return typed
projections.

| Route                          | Does                                             | Reply                                                       |
| ------------------------------ | ------------------------------------------------ | ----------------------------------------------------------- |
| `POST /api/tree/stop`          | Stops a node and everything it summoned          | `views.node_view`, `views.tree_view`, the engine's sentence |
| `POST /api/tree/background`    | Sends a running node to the background           | the same three                                              |
| `GET /api/tree/node/{node_id}` | One execution's messages, by id or unique prefix | `views.node_view(…, include_messages=True)`                 |
| `POST /api/mcp`                | Enables or disables one server for the session   | `views.session_view` plus the shell's MCP state             |
| `POST /api/external-files`     | Allows or denies reads outside the workspace     | `views.session_view`                                        |
| `GET /api/context`             | Window usage and the session's bill              | `views.context_view`                                        |
| `GET /api/debug`               | Full content-free diagnostic snapshot            | `DebugDiagnostics.read`                                     |

`/context` in an attached terminal paints that projection and the workspace from
`/api/state`. It shows no scope section — the active directories and the
instruction files have no projection on the server, so the report omits them
rather than filling them from local trackers, which would describe a session the
service is not running. A datum the service did not send is named as absent; it
is never printed as zero, because a zero is a claim. A cold session is the
exception that proves the rule: the service reports the real zero there, so the
screen reads `0 (0%)` and never `?`.

The tree actions answer with the whole tree, not only the node: stopping a node
stops its descendants, and a client that repainted one row would leave the
branch saying it is still working.

The two toggles are also readable from `/api/state`: `views.session_view`
carries `external_files` and `mcp_overrides`, because the row is where they live
and a command that changed one has to be visible in the projection the screen
paints from, not only in its own reply.

## What is refused, and with which status

HTTP status distinguishes an invalid request from an unavailable or failed
operation.

- **`400`** when the request names something that does not exist: a node
  reference that matches nothing, or an MCP server the config does not declare.
  `ServerRuntime.node` translates the summoner's `SummonError` into a
  `LookupError` for exactly this distinction. A body of the wrong type is `422`
  from the schema; `allow` is a `StrictBool`, because a lax bool would read
  `"yes"` as consent to a permission change.
- **`409`** when the node exists and cannot take the action now: the session's
  own agent, or a node that already finished (`SummonError`). The two toggles
  are `409` too while a turn runs, and `/api/mcp` is also `409` while a subagent
  is still working — the terminal's two criteria, not a new rule. A live node
  runs on an agent whose tools were computed when it started, so disabling a
  server would close the transport under a call already in flight and enabling
  one would swap the tool set mid-run. Both live where the decision is: the turn
  check is `ServerRuntime._refuse_while_busy`, the subagent check is
  `Composition.apply_mcp_override` reading `summoner.running()`, because the
  terminal's copy of that rule lives in `commands_mixin.is_refused_while_busy`
  and the engine is where both clients can share it.

Every route that refuses through `_refuse_while_busy` says the same thing, in
one place: `A turn is running: finish or stop it before you <action>.` Starting
a session, opening one, switching workspace, switching agent, rollback and fork
all go through it, so a client never has to parse a second wording for those.

Two routes are the exception, and it is part D's: `POST /api/model` and
`POST /api/effort` are refused by the engine itself — `BusyError` from
`ModelService.select` and `set_effort` — with
`A turn is in progress. Wait for it to finish (or stop it) before changing the model.`
The status is the same `409`; the caller tells them apart by the route it
called, not by the sentence, and unifying the wording was not part of that cut.

One asymmetry is the engine's and is carried over, not invented: `kill` on a
finished node _reports_ that it had already finished and changes nothing, so
that request is a `200` carrying the sentence; `detach` refuses it, so
backgrounding a finished node is a `409`.

A refusal changes nothing. The tests assert it by comparing `views.tree_view` or
the session row before and after, not by trusting the status code.

## The context meter: what exists, and what does not

`ContextMeter.snapshot` and `session_cost` are enough for what the sidebar
paints: the **projected** usage, its percentage of the window, the budget split
and the bill. The projection keeps the terminal's rule that a cold session with
a known window is at `0%` rather than `?`.

Two honest limits travel with it:

- **The fixed blocks are a measurement, not a constant.** `tools` and
  `instructions` come from `ModelRequestParameters`, which exists only while a
  request is being made. Their last value is stamped on the session and
  `Composition` now restores it on construction and on `bind_session`, so the
  budget of a loaded session is not optimistic. The terminal's own
  `bind_session` still carries the same call; that copy goes in `tui-attach-v1`,
  when the terminal stops binding state locally.
- **The history is the checkpointed one.** The turn loop checkpoints about once
  a second, so during a turn the newest steps can be a moment behind the
  transcript the stream is painting. The route says `measured` and `projected`
  as they are rather than projecting a history the server does not have yet.

`GET /api/debug` exposes per-request measurements from the service that runs the
turn: cache usage, compaction decisions, request shape, and timings. The
terminal renders those measurements without owning a run or context meter.
Themes remain presentation. `goto` remains navigation inside a tree the client
already has; neither needs a service mutation.

## What is still duplicated

`views._turn_preview` and the terminal's `rollback_mixin._first_line_of_turn`
build the same line from the same engine call, `prompt_of_turn`: the prompt that
opened a turn, collapsed to one line and cut, with the image count appended.
They stay two copies for this cut for the same reason part D's selection copies
do — the terminal still owns its rendering and is not migrated yet, and a shared
helper would need `rollback_mixin` to consume it in the same change or it would
only add a third copy. The unification belongs to `tui-attach-v1`, when the
terminal stops building its own previews.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
