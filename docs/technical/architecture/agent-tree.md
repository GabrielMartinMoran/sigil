# The agent tree through the service (part H)

The tree window reads and drives the service's tree. Before this cut it read
`SigilApp._summoner` and `_agent_tree`, and both are `None` on an attached
terminal: `ctrl+t` opened an empty list and every action on a row did nothing.

## Where the nodes come from

There is one source of truth per mode, and the mode is one attribute:

| Mode       | Nodes from                                        | When                  |
| ---------- | ------------------------------------------------- | --------------------- |
| Attached   | `state['tree']['nodes']` of the last `/api/state` | `_client is not None` |
| In process | `_agent_tree.active.nodes()`                      | the test seam only    |

`TreeMixin.tree_nodes()` picks by `_client`, and `open_tree` re-reads the state
before pushing the screen, so the window is the truth as of the keypress instead
of the last event that moved a node. A second source of truth would be a list
that disagrees with the agent it describes.

The projection is JSON; the screen paints `AgentNode`.
`tree_mixin.nodes_from_tree` and `node_from_view` are the one place the two
meet, and the widget is untouched: ids, tree position, state, mode, task, runs
and budget travel, and `elapsed` is turned back into a clock — a finished node's
clock stops where the service stopped counting, a running one keeps going. An
entry that does not read back is dropped, the same rule a stored tree keeps.

## `ctrl+t` always opens

The window opens with one subagent, with forty, and with none. The empty case is
drawn by the list itself: the status line says
`No subagents in this session yet.` and the row
`Subagents appear here while the agent works.` The old `NO_TREE_NOTICE` — "this
session has no agents yet" — now belongs only to an in-process app built without
a tree; an attached terminal never shows it, because the session does have an
agent and the service is the one that knows.

## Actions are the service's

The client requests an action; the service owns the execution state and returns
its projection.

| Key     | Route                                           | What changes                                         |
| ------- | ----------------------------------------------- | ---------------------------------------------------- |
| `enter` | `GET /api/tree/node/{id}?include_messages=true` | the node view, painted from the service's transcript |
| `k`     | `POST /api/tree/stop`                           | the node and everything under it, in the service     |
| `b`     | `POST /api/tree/background`                     | `mode` becomes `async`, in the service               |

`ServiceClient.tree_node`, `stop_node` and `background_node` are the three
calls. They carry the client's existing failure vocabulary: `400` is
`RequestRejected` (a ref that names nothing), `409` is `ServiceBusy` (the root,
or a node that already finished) with the engine's own sentence, and
`failure_notice` prints it. `include_messages` is **advisory**: the route always
returns `transcript`, and the flag documents the caller's intent rather than
switching the wire.

The transcript is painted through `ChatLog.render_items`, the same projection
renderer the main transcript already uses, because `AgentView` rebuilds its body
from `ModelMessage`s an attached terminal never holds. The widget keeps its type
and its header; only the body's source differs.

## What is left of the local path

It is the in-process seam and nothing else. `TreeMixin` keeps the summoner calls
(`summoner.kill`, `summoner.detach`, `summoner.resolve`) behind
`_client is None`, which is the same fence part G describes for `run_turn`:
those paths serve the widget tests that mount `SigilApp` with an engine. No
production entry point can reach them, and nothing new may grow in them that
both modes need.

## Keeping the list current

`tree_changed` is a state change, so it lands in `_apply_service_state`, which
now calls `_refresh_tree_screen()`: an open tree repaints from the freshly read
projection. On an attached terminal `on_tree_changed` does not read the node out
of the event — that node is not one this process holds — it refreshes the state
and, when a subagent is open, re-reads that node with `include_messages`. No
timer and no fixed sleep: the repaint is driven by the event and by the state it
asks for.

## The tests

`tests/tui/test_ui_service_client.py` proves the three methods against the real
application (happy path, `400` for an unknown node, `409` for the root and for a
finished node) and `tests/tui/test_ui_client.py` proves the window: empty tree
opens and says so, a service subagent appears in the list, `k` and `b` change
the node in the service and the list shows it, `enter` paints the transcript the
service keeps, and a published `tree_changed` repaints an open list. Every wait
is for a rendered row or a service fact, never a sleep.

## The wake, in attached mode

A background subagent outlives the turn that started it. The turn ends, the user
reads its answer, and the subagent's report arrives afterwards. The terminal
answers that with a turn of its own; the service has no timer to do it with, so
the wake is a durable input the service gives itself.

The seam is `SessionRuntime._consume`, the one place that starts turns. When the
queue has nothing to claim, the consumer asks whether a report is owed and, if
one is, accepts it as an ordinary `turn` row before claiming it. Nothing outside
`_consume` starts a turn, so a wake cannot compete with a user's message.

The decision has two facts and no clock:

| Gate                           | Why                                                                                                                                                |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| No subagent is `running`       | Three researchers that finish together are three reports and **one** turn; the question is whether the squad is quiet, not how long ago it was not |
| At least one report is pending | Nothing to read, nothing to start                                                                                                                  |

`_on_tree_idle` keeps publishing `tree_idle` and now also nudges the consumer,
clearing `_settled` so `wait()` keeps meaning "nothing left to do" while a
report is unread.

## What wakes a turn, and what never does

Only `done` and `failed` reports wake. A cancelled node is refused: it keeps
whatever it had said and keeps `delivered` false when the cancellation arrived
before anyone read it, so handing that to a turn would execute again the work
somebody stopped on purpose. A failure is the opposite case and does wake — the
parent has to see it. The rule lives in `SubagentSummoner.take_reports`, beside
`_deliver`, which decides in the same place which nodes send anything at all; a
second copy in the service would drift from it.

Three more refusals: a closing service starts nothing; a turn already running
leaves the reports for the wake that follows it; and a report an interrupted run
left behind is never re-executed — the row is durable, so `start` marks it
`interrupted` and `claim_next` does not claim it.

The model prompt is the reports' envelopes followed by `WAKE_PROMPT`. Display
classification is separate from that text: the service persists the input's
`origin` and report snapshots with the accepted row. Each run's `MessageTypes`
capability restores per-part types in message metadata after the model library
merges request envelopes.

Both transcript adapters use `src.core.agents.message_types.input_views`. It
emits one `subagent_report` item per saved snapshot, keeps explicitly marked
user text as a user item, distinguishes agent messages, and hides internal
notices. Untyped historical parts remain user input, even when their text looks
like a report; neither prompt text nor the current tree determines the type.
Window counts use the same projection policy, so pagination preserves item
boundaries. The terminal paints a report as its clickable `SubagentReport` card,
and the web client paints it as a collapsible line.

## The tests

`tests/tui/test_server_runtime.py` proves the wake against the real composition
and the real engine bell (`SubagentSummoner._deliver`, the last thing `_execute`
does for an async node): a report that lands after the turn starts exactly one
turn and reaches the durable transcript; two reports of one squad produce one
turn with both; a cancelled node wakes nothing and never appears in the
transcript; a failed node does wake; and a closing service stays silent. Every
wait is a bounded deadline on a service fact, never a sleep.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
