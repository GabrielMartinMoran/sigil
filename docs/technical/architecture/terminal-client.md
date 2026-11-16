# The terminal attached to the service (part G)

The terminal no longer executes anything. `src/sigil.py` — the only production
entry point — asks [part C](service-launcher.md#the-launcher-part-c) for a live
service, reads the state it projects, and builds the window over a
`ServiceClient`. There is one executor per session: the service.

This part is deliberately narrow. It moves **the conversation** onto the client
and leaves every other panel where it is, so each remaining command can move
with its own cut instead of half-moving all of them at once.

## What moved, and what did not

The terminal retains presentation while delegating durable actions to the
service.

| Area                      | Now                                                                                         | Consequence                                                                                                                                                                                                                                |
| ------------------------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Sending a message         | `POST /api/turn` through `TurnMixin.submit_to_service`                                      | the composer stays usable; the service decides turn or steer                                                                                                                                                                               |
| Streaming                 | `GET /events`, applied by `SigilApp.apply_service_event`                                    | deltas, tool calls and results, turn boundaries, notices                                                                                                                                                                                   |
| Steering                  | the same send, with the queued line marked until `steer_delivered`                          | the run reads it at its next seam, as before                                                                                                                                                                                               |
| Stopping                  | `POST /api/abort`, after the same double `Esc`                                              | the service kills the shells and cancels the run                                                                                                                                                                                           |
| Leaving a node            | `Esc` inside a subagent goes back to whoever summoned it                                    | the abort is asked _after_ that, not before: a node is usually open while a run is in flight, and the old order armed the confirmation on every press so the reader could never leave. Stopping is still two `Esc`s, from the conversation |
| Leaving                   | nothing is sent on the way out: `/quit` and the window key close the stream and detach      | the service keeps running, another terminal's turn is untouched, and [idle shutdown](#leaving-a-terminal-detaches-the-service-keeps-the-work) is what ends it                                                                              |
| Questions                 | the same `QuestionPanel`, answered with `POST /api/answer`                                  | the panel's keys and layout are unchanged; a question counts as answered only once the service has taken the answer, and a failed send is offered again (see [the question deadline](#the-question-deadline))                              |
| Images                    | ordered `OutgoingImage`s with the `[Image #n]` marker                                       | the picture that was attached is the one stored                                                                                                                                                                                            |
| Reconnection              | `events(after=last_event_id)`, then a state read                                            | a gap is announced, never silently painted over                                                                                                                                                                                            |
| Restart recovery          | `ServiceClient.refresh_endpoint()` before the retry, then a fresh `/api/state`              | the client re-discovers through the injected launcher, rebuilds its pool over the transport it was given, and restarts the cursor with the new run (see [When the service restarts](#when-the-service-restarts))                           |
| Commands                  | `handle_command`, through `src/ui/client/command_transport.py`                              | client commands run here; core commands name the route that serves them and what is still missing (see [Which commands run here](#which-commands-run-here))                                                                                |
| Provider, agent, rollback | unchanged, still local                                                                      | those panels read files and rows this process can see                                                                                                                                                                                      |
| The agent tree            | `state.tree`, `POST /api/tree/stop`, `POST /api/tree/background`, `GET /api/tree/node/{id}` | read and driven by the service; see [part H](agent-tree.md#the-agent-tree-through-the-service-part-h)                                                                                                                                      |

The dispatcher is not replaced any more. An earlier version swapped
`handle_command` for a single refusal, which was honest about the missing
senders and wrong about everything else: it refused `/theme` along with
`/rollback`. The decision now comes from one table, read once, at the choke
point every path goes through.

## The question deadline

A question waits for an answer without a deadline by default. To set a deadline,
use `SIGIL_QUESTION_TIMEOUT` or the `question_timeout=` argument of
`ServerRuntime`. The value must be a positive number of seconds; an invalid
value raises an error.

When the deadline passes, nothing is answered on the user's behalf. `_ask`
resolves the waiting future with `None` — the value a cancellation already means
— so `ask_user` takes the path it has always taken: it cancels the run, the turn
ends where it stood, and `turn_finished` reports `cancelled`. The question
itself is published as closed, and the person who comes back to the screen is
told why:

- `question_closed` carries `id` and `reason`. The reason is `answered` or
  `cancelled` when a client resolved it, `timeout` when the deadline did,
  `closed` when the process left with the question still open, and `withdrawn`
  when the turn stopped first. A client that only needs the panel to stop asking
  reads `id`; the reason is there for the one that wants to say what happened.
- A `notice` with tone `warn` says the question went unanswered, that the turn
  stopped, and that sending the message again continues. Without it the panel
  would simply vanish, which reads as a bug rather than as a deadline.

`ServerRuntime.close()` releases an open question with `reason='closed'` before
anything else. `abort` and the turn's `finally` already did it on their paths; a
graceful stop or a failing start did not, and the future left behind had a task
awaiting it that no longer had a client.

The terminal's half is a marking rule, and it lives in
`SigilApp.answer_service_question`: a question is recorded in
`_service_answered` **only after** `POST /api/answer` succeeds. A send that
fails — a service that restarted, a connection that moved — shows
`failure_notice(exc)` and returns without marking, and the claim on the panel is
released in the `finally` so the next `/api/state`, or the reconnection, offers
the same question again. Marking it on the way out instead would leave the
service waiting for an answer while the screen showed a prompt with nothing left
to answer.

Identity is the id (`q1`, `q2`, …), never the text: two questions asking the
same words are two questions, and both can be open in one session's lifetime.

## Which commands run here

The catalogue decides **who may serve a command**. The transport table decides
**whether this terminal can reach that server**, and the two are different
questions: the first is shared vocabulary, the second changes with each cut.

`src/ui/client/command_transport.py` holds one row per command. A row carries
the name, the owner the catalogue already declared, the route the service serves
it at, and exactly one state:

| State       | Meaning                                                         | What the terminal does                                   |
| ----------- | --------------------------------------------------------------- | -------------------------------------------------------- |
| `client`    | this window does the work                                       | runs the handler here; sends nothing                     |
| `delegated` | the service does the work and a sender exists                   | calls the route                                          |
| `not_wired` | the service does the work and has the route; no sender here yet | one notice: the command, the route, and what is missing  |
| `blocked`   | the service has no endpoint for it                              | one notice: the command and why there is nothing to call |

Eight commands are `client`: `/theme`, `/sidebar`, `/debug`, `/alerts`,
`/tools`, `/reasoning-visibility`, `/help` and `/quit`. They repaint this
window, so they never travel, and an attached terminal runs them exactly as one
that owns the engine does.

Nine core commands are `delegated`: `/context`, `/external-files`, `/mcp`,
`/effort`, `/agent`, `/new`, `/load`, `/model` and `/rollback`. Their handlers
send to the service and paint the answer it returns, and none of them writes the
session locally — the row it changed belongs to the service, and a second copy
here would be a second truth about one conversation. The pickers stay in the
terminal; only the list and the effect travel.

`/agent` is the clearest case of that rule. The switch is `POST /api/agent`, and
what lands on screen afterwards is the service's `state.session.agent` — the
terminal no longer stamps `session.agent` itself, because the profile a session
runs under is a fact about the session and not about the window looking at it.
For the same reason a second attached terminal shows the new agent after it
refreshes, without either one telling the other anything.

`/new` and `/load` repaint the transcript and restart the event cursor from the
state they just read, instead of reusing what the terminal had: the cursor is a
position in the running service's sequence, and an old number would silently
skip everything the new session publishes. Opening a session can also move the
service to another workspace; the header follows the projection, not a local
guess.

One core command is still `blocked`: `/default-models`. The screen edits the two
config files that decide what a profile starts on, and those files belong to the
service — a terminal that wrote them would be a second writer on one
configuration, which is the failure the attached terminal exists to remove. The
service serves both routes now (see
[the defaults the terminal could not edit](#the-defaults-the-terminal-could-not-edit)),
so what the row is missing is a sender and not an endpoint: it stays `blocked`
until `model-defaults-client-v1` points one at `GET`/`POST /api/model-defaults`,
and until then the dispatcher answers it at the door with `unavailable_notice`.
That sentence — _"a service command, and the service has no endpoint for it
yet"_ — is one cut behind the service, and `show_default_models` is still never
reached attached: its screen and its collaborators belong to a terminal that
owns the engine, so that handler has nothing to say about a mode it cannot run
in.

That row is the single exception, and `test_only_the_documented_gap_is_blocked`
pins the blocked state to exactly it. `not_wired` stays empty: it would promise
a sender that is not coming in this cut. The decision has to be total — a
command whose row says it cannot run here must say so precisely rather than
borrow another answer. Unblocking the row is the whole of
`model-defaults-client-v1`, and it is the only change it makes to this table.

## The defaults the terminal could not edit

`/default-models` was the one command with nothing to call: the two files that
decide what a profile starts on belonged to the service, and no route projected
them. The service owns them again, so it serves them; pointing the terminal's
sender at these two routes is the next cut.

| Route                      | Does                                                             | Reply                                |
| -------------------------- | ---------------------------------------------------------------- | ------------------------------------ |
| `GET /api/model-defaults`  | Both config files, per file, and the accounts their entries name | `views.model_defaults_view`          |
| `POST /api/model-defaults` | Sets or clears one entry in the file the destination names       | the same projection, after the write |

The read is per file and never merged. `files` carries each destination's path,
whether it exists, the `default_model` block, the `agents` map, the entries the
reader rejected, and the sentence that says why; a file that is not there yet
projects as an empty one, which is exactly what the reader answers for it.
`targets` carries the merge instead: one row per target — `null` for the block a
conversation starts on, then every agent either file mentions — with the entry
that wins, the files that mention it, and whether that entry is the one the
reader could not parse. The precedence stays in `ModelDefaultsState`, so no
client holds a second copy of it. `accounts` names the aliases the entries use
and whether the router has each one, which is what lets a screen say _"account
`work` is not configured"_ instead of printing an alias nobody can place.

`POST` is the engine's (`Composition.save_model_default`), and so are its
refusals. A config file that cannot be read is not rewritten, and the answer
carries the engine's own sentence. A turn in the way answers `409`, which is the
rule the terminal's `/default-models` already applies (`BUSY_COMMANDS`): a
subagent reads `agents.<name>` the moment it is summoned, and the subagent
criterion belongs to `/mcp`, so it is not added here. `400` is for a request
that names something unusable — a destination that is not `project` or `global`,
an agent name the config cannot hold, a level `parse_choice` does not know —
because a different request would work. Clearing an entry is `{"config": null}`
and has to be spelled out, so a field a client forgot cannot delete one. The
reply is the state the write left, re-read from disk, so a client repaints what
is there rather than what it asked for.

The files stay the service's. Both directions go through the composition that
owns them — the same reader and the same writer the in-process terminal uses —
which is what keeps one configuration from having two authors.

## The help is derived, never repeated

Every listing of commands reads the same two tables:
`src/core/commands/catalog.py` for a command's name and sentence, and
`src/ui/client/command_transport.py` for whether this terminal can act on it.
`src/ui/render/help_text.py` renders a row as the catalogue's words plus
`listing_mark`, and `/help` and the `/` menu both call it — the help with
`attached=self._client is not None`, the menu with the flag its mixin passes
down.

Nothing else may state a command's availability in its own words. A second copy
of "why this cannot run here" is a second thing to keep in step with the table,
and the copy that drifts is always the one the user reads. So the mark is three
words the table justifies, the sentence a refused command prints is
`unavailable_notice` and only that, and a handler in a mode it cannot be reached
in says nothing at all rather than answering for it.

`/provider` was the last one, and it needed a route rather than a sentence.
`GET /api/accounts` lists the accounts with `has_credential` as a boolean — the
key itself never leaves the process that stores it, and no route, log line or
error message echoes it. `POST /api/accounts` adds an API-key account and makes
it active, bounded at thirty seconds because it is the one call that reaches the
provider with the user's key; `POST /api/accounts/{alias}/activate`,
`POST /api/accounts/{alias}/rename` and `DELETE /api/accounts/{alias}` complete
the set, and all four answer `409` while a turn runs, before any slow work
starts.

Two rules are worth naming because they are refusals, not conveniences.
**Deleting is refused for the active account**: the engine would promote a
successor, but only a person at the terminal sees the model change under their
feet, so the route insists on activating another account first — and with a
single account that is also the answer, since it is the active one. **Browser
sign-in is not offered over HTTP**: the OAuth flow needs a browser and the
process that owns it, so an OAuth request is refused with that reason and the
account has to be added from the terminal running the engine. That is a real
limitation, not a missing wire.

`/model` needed one route the service did not have: `GET /api/models` lists the
accounts, not the models of each one. `GET /api/models/{alias}` answers that
list from `ModelService.catalog_for` — the same call the in-process terminal
makes — and bounds the wait at fifteen seconds so a provider that never answers
becomes a refusal with a sentence instead of a request that stays open. When the
provider cannot be read, the route refuses with the engine's own reason: the
fallback list the in-process terminal shows next to a warning is deliberately
**not** offered here, because a picker listing options the provider did not send
is exactly the invented answer a delegated client must not produce. The picker
itself stays in the terminal, built from that projection.

`/rollback` needed the prompt of the turn it drops. The rows that hold it are
truncated by the rollback, so the route reads it first — `prompt_of_turn` — and
carries the text in the `rollback` reply. That is the field the in-process
terminal used to look up before applying; projecting it changes no count and no
row, and it means no client needs a database of its own. Images travel with it:
`prompt_images` carries each picture as base64 plus its media type and
identifier, so the composer restores the message with the picture its
`[Image #1]` marker names. The service re-encodes an image when it stores it, so
a client receives the stored bytes and not the sent ones; the pixels are what
match. The fork reply already carried its session id, so `/fork` needed nothing
on the server side.

**Still uncovered.** Three gaps this cut leaves open:

- **OAuth over HTTP.** Browser sign-in needs the process that owns the flow, so
  the API refuses an OAuth request with that reason instead of faking a key.
- **`SecretRejectedError`.** The client translates the class, and no test drives
  that path.
- **An empty catalogue on `activate`.** Activating an account whose provider
  returns no models has no test.

A command that cannot act answers in one notice, opens no panel and writes no
queue row. An unknown command keeps its own answer, because a name the catalogue
never declared is not a transport decision in the first place.

The order at the choke point is the decision, in this order: a `client` row runs
here, a `delegated` row runs its handler (which sends), and a `not_wired` or
`blocked` row answers once and returns.

A delegated command whose action would change the session is refused **before**
it opens anything, while a turn runs. The terminal reads `busy` from
`/api/state` — the same fact the service would use — so `/agent`, `/model`,
`/effort`, `/new`, `/load`, `/mcp` and `/external-files` answer with one line
instead of opening a picker, fetching a catalogue and only then being told the
session is in the way. The sentence is the service's own ("a turn is running:
finish or stop it before you …"), so there is one wording for one fact rather
than two that drift apart.

Not every `409` is that fact, though. `/api/session/open` and `/api/rollback`
also use it for a turn this session cannot reach or a session that does not
exist, so `failure_notice` reads the service's sentence: when it is not about a
running turn, the notice repeats what the service said instead of claiming the
agent is busy. A rollback that could not restore files says so in the same words
the in-process terminal uses, because both call one `_files_report`.

The table is imported against the catalogue and fails at import when a row is
missing or extra, so a new command cannot become a menu entry that silently does
nothing. Two tests hold the same line: `tests/tui/test_commands_ownership.py`
for the table and `tests/tui/test_ui_client.py` for the behaviour behind it.

The three side doors — `ctrl+m`, `ctrl+l` and `ctrl+a` — call `handle_command`
instead of `open_models`, `open_effort` and `open_agent`. A key is another door
into the same room, and a door with a path of its own is a door that answers
differently. Because `/effort` is delegated, `ctrl+l` opens the picker fed by
the service rather than answering a refusal; the levels it offers are the ones
`model_view.efforts` reports for the model the service is actually running.

## The first connection starts after the state, not at zero

`/events` keeps a buffer for reconnections. A terminal that opened the stream at
sequence zero would be handed everything still in it — a transcript the state it
just read already describes — and paint it twice. So the first connection starts
at `state['seq']`, and only a reconnection uses the last event applied. The two
cursors answer different questions: _what have I already got_ versus _what did I
miss_.

## When the service restarts

The address is fixed when the client is built — every other request depends on
that being a fact rather than a guess — so a restart cannot be absorbed
silently. The follow loop is where it is handled: when nothing answers
(`ServiceUnavailable`), it asks `ServiceClient.refresh_endpoint()` before
dialling the same dead address again.

That call re-discovers the service through the launcher the client was
constructed with, compares the result with the endpoint it holds, and rebuilds
only when host, port or token differ. Rebuilding is two steps: `_aim` takes the
new address and credential, and `_build` makes a new pool over **the transport
the client was given** — a rebuild that dropped it would dial the network from a
test that handed the client a double — after which the old pool is closed.

The cursor is the part that is easy to get wrong. Sequence numbers are **per
run**: a restarted service numbers from one again. Reusing the cursor this
screen had against the old run would open the new stream _after_ a number the
new service has not reached, and the terminal would follow a live conversation
while silently skipping everything published before it caught up. So the
recovery resets the cursor to zero, reads `/api/state` from the new service and
takes the cursor from that answer — the same rule the first connection follows —
then repaints the transcript from the new run's durable rows.

The answered questions go with the old run for the same reason. Question ids are
per run: a service numbers them `q1`, `q2`, … from one on every start. The
recovery clears `_service_answered`, because an id kept from the previous run
suppresses a brand-new question that happens to reuse a number the old run
already resolved — and the service then waits out its deadline for an answer no
screen will ever offer.

In practice the screen ends up following whatever the new run has, which is not
necessarily the conversation it was showing: the old conversation is durable,
but a restarted service opens its own session and reattaching the terminal to
the previous one is not part of this cut.

## What is painted when the terminal attaches

The stream continues a conversation; it is not one. So attaching paints the
conversation the service already has, from `/api/session/durable` — one coherent
read that carries the projected transcript and the queue together:

| Painted            | From                              | Detail                                                                                                                    |
| ------------------ | --------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| A user line        | `transcript` items of kind `user` | the text as it was typed, `[Image #n]` markers included, plus the description of each image (`1 image (640×480 PNG)`)     |
| An assistant block | `assistant` and `thinking` items  | the answer and its reasoning, grouped per response                                                                        |
| A tool call        | `tool_call`, then `tool_result`   | name, arguments and the result the service presented, paired by `call_id`; a call with no result stays marked interrupted |
| A waiting message  | `pending`                         | painted with the queued mark, and retired when the service confirms delivery                                              |

What is **not** painted, and why:

- **Timing on stored calls.** The projection carries no duration for a call that
  already finished, so a stored tool row shows its outcome and not `120 ms` —
  and a repainted conversation loses it for the same reason. Inventing one would
  be a number nobody measured.
- **A subagent's transcript.** The tree is a later cut; the durable read of the
  root execution does not carry another execution's messages.
- **Messages that exist only in a recovery copy.** They are listed as recoveries
  and never merged into the transcript: merging two conversations would
  misrepresent both.

The queue is painted as _waiting_ rather than as history for the same reason the
state exists at all: a message the run has not read is not part of the
conversation yet. The confirmation event carries the service's own enqueue id,
not the request id the terminal generated, so waiting lines are retired in queue
order — the oldest one is the one the run read. A line painted from the durable
read is removed on confirmation (it now belongs to the transcript); a line typed
in this window only loses its mark, which is what a live turn has always done.

A gap during a live turn **discards the block in progress**. The transcript is
rebuilt from the rows and the deltas that follow open a new block, because an
unknown number of deltas never arrived: a conversation with a hole in it reads
better for a second and lies for as long as it is on screen.

The **end of a turn repaints from the rows unconditionally**. A delta can be
lost, a result can land with no call above it, and a call can arrive with no
block to hold it, so a screen painted from events alone cannot tell a turn that
ran no tool from one whose tool it missed — the rows can, and `turn_finished` is
the one moment they can be painted without dropping text still streaming in.
`_finish_turn` therefore rebuilds after `_turn_active` is cleared, not only when
another client's message deferred it.

## The chrome comes from the state too

An attached terminal composes no `WorkspaceManager`: the workspace is the
service's. So the other half of the window title and the status bar's project
and worktree are read from `state['workspace']`, the same projection
`views.workspace_view` already builds for the web header:

| Painted                       | From `state['workspace']`        | Rule                                                                                                                    |
| ----------------------------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Window title, repository half | `repository` and `is_worktree`   | `sigil` for a checkout, `sigil:better-debug` for a worktree — the label is what tells two trees of one repository apart |
| Window title, folder fallback | `root`                           | outside git there is no repository name, so the folder is what is left                                                  |
| Status bar, project           | `root`                           | the directory name, exactly as the in-process bar shows it                                                              |
| Status bar, worktree badge    | `label`, `branch`, `is_worktree` | the same `WorkspaceBadge` the manager produces                                                                          |
| Diff                          | `root`                           | polled off-thread against the service's root, not a local path                                                          |

Before this, both halves asked a manager that an attached terminal does not
have, and painted nothing while the answer sat in the state the client had just
read. A projection that has not arrived yet paints nothing — an empty title is
honest, a locally guessed one is not, and the state is the only process that
knows where the conversation runs.

## The image capability is the projection's answer

`ModelSnapshot.images` defaults to `True`, which is harmless inside the process
that owns the model and wrong the moment it crosses a boundary as a fact. So
`views.model_view` projects `images: null` while no model is configured —
`configured` is the field that says whether the rest of the model projection
means anything — and a real boolean only when a model is known.

The terminal's `accepts_images()` follows that: attached, it reads
`state['model']` and answers `None` for _unknown_, never `True` by default. The
composer refuses the image either way; what changes is the sentence the user
reads. It says the capability cannot be known and to pick a model with `/model`,
instead of accepting the picture and letting the provider refuse it after a
whole turn was spent. This is a wire contract: `null` now means _unknown_, and a
client that reads the field as a boolean has to handle it.

## Leaving a terminal detaches: the service keeps the work

The service outlives a terminal by design: closing the window is not a reason to
interrupt work, and the service stops itself when that work ends (see
[idle shutdown](service-runtime.md#idle-shutdown)). Leaving is that and nothing
more — `/quit` and the window's own quit key both close this client's stream and
detach it. **No exit path sends anything.** Sending a stop from the exit was the
bug: `abort` is global to the service, so closing one terminal cancelled the
turn a user had started from another one, and because the session is saved at
cancellation, the message only then appeared in the transcript.

`POST /api/stop-all` still exists and is unchanged — `ServerRuntime.stop_all()`
calls `abort()` (shells killed by process group, the run cancelled, open queue
rows stopped) and then `summoner.cancel_all()`, which is the half `abort` cannot
cover: with no turn running a background subagent would otherwise keep spending.
Nothing in the route kills the service's process. What changed is the caller: no
client calls it on the way out. Stopping the whole session is a deliberate act,
never a side effect of closing a window, so the route is there for an explicit
command rather than for an exit.

The one thing that ends the service is therefore the idle window: when no row is
open and no turn runs for `DEFAULT_IDLE_GRACE` (900 s), the watch asks for a
graceful stop. A closed terminal is not part of that decision — it is not work,
so it neither keeps the service alive nor ends it.

The attached terminal's queue overlay is the other half of the same contract,
and it is painted per row rather than per turn. The service announces an
acceptance with `state_changed` and `reason='queue'`, carrying the fact and not
the text; the screen that did not type the message re-reads
`/api/session/durable` and paints the waiting lines from `pending`. Only the
overlay is repainted — the transcript is rebuilt from the rows when the stream
stops — because clearing the chat mid-turn would drop the deltas streaming into
the block being written. That is what used to keep another terminal's message
invisible for the whole turn while its screen only said "Thinking".

A turn that ends with something to report asks for attention. The request is
held until the squad is quiet, and the squad is the service's tree, not this
process's: `settle_attention` stores the event, and `subagents_running` counts
the node statuses of the state projection.

Counting locally would report zero the moment the turn closed, because a
subagent outlives the turn that summoned it. The notification would fire while
the work still ran and call the user back to a screen where nothing was ready —
the early ring this waiting exists to prevent.

The summoner remains the fallback for the in-process seam, which has no
projection to read. Attached, the client never touches it.

## What the terminal still holds locally

Three things, and none of them can execute a turn:

- **Preferences and themes** are this client's own: they describe the window,
  not the conversation.
- **The agent profiles and the roadmap** are files and rows it reads exactly as
  the service does, for the panels that have not moved. `src/sigil.py` still
  builds a `RoadmapStores` for the attached terminal, because other panels and
  commands keep using it, but the sidebar no longer reads it: the plan on screen
  is the service's projection. The reason is below, under _Where the sidebar
  reads each section_.

The model service is **not** among them. Building it is building an engine, so
no part of the attached terminal constructs one.

## Where the sidebar reads each section

Attached, the sidebar paints the service's projection and measures nothing
itself. `refresh_sidebar` branches on `self._client is None`: the in-process
path below is untouched, and the attached path reads `_service_state` plus one
meter route.

| Section          | Source in attached mode                                                            | What is _not_ done                                                                                   |
| ---------------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Model            | `state['model']`: `account.alias`, `account.kind`, `model`, `thinking`             | no `ModelService` is built; the route does not project an auth label, so the panel does not show one |
| Context and cost | `GET /api/context` through `ServiceClient.context()`, after `_apply_service_state` | no local `ContextMeter`; the terminal's history is empty by construction                             |
| Loaded skills    | `state['skills']`, derived by the service from its own transcript                  | no `viewed_skills()` over a transcript this process does not have                                    |
| MCP              | `state['mcp']['servers']`, keeping `enabled` entries with `ok = not error`         | no probe: connecting here would open a second set of subprocesses                                    |
| Roadmap          | `state['roadmap']`, the root execution's plan                                      | no `RoadmapStore`                                                                                    |

Two rules decide what a gap looks like:

- **Absent is unknown, never zero.** A key the projection does not carry is
  painted `unknown — the service did not report it`. `none yet`,
  `No tasks yet.`, `not configured` and `0%` are claims about a session; a
  missing key supports none of them.
- **A zero that the service sent is real.** A cold session with a known window
  answers `percent: 0.0`, and the panel paints `0%`. `mcp.loading: true` is the
  exception on the other side: the layer has not answered yet, so it is treated
  as not reported rather than as an empty list.

The plan is read from the projection instead of the terminal's own
`RoadmapStores` for one reason. `RoadmapStore` loads its rows once, in its
constructor (`src/core/roadmap/roadmap_store.py`), and only `reload()` or
`bind()` refreshes them; `RoadmapStores.for_node` caches the store per node. The
service writes the plan through its own store
(`src/core/composition/bootstrap.py`) and projects it in `/api/state`, and
nothing in the attached terminal subscribes to those writes. The local copy is
therefore a photograph taken at mount: showing it would be showing frozen rows
without saying so. The service's copy is the only one that moves.

Repainting needs no timer. `apply_service_event` already routes `state_changed`
to `refresh_service_state`, which applies the state and calls `refresh_sidebar`,
and `on_mount` takes the same path through its first `refresh_service_state`.
The context line is the one asynchronous read, on an exclusive worker
(`sidebar-context`), so a newer measurement replaces an older one instead of
queueing behind it.

The cost mark this mode cannot reproduce is `+`. `/api/context` projects
`total`, `approximate` and `requests`, but not the `unpriced`/`unmeasured`
counts that make a total a floor; the panel shows the amount without inventing
the mark. Widening the route is the fix if that mark is wanted, not guessing it
here.

## What this process measures, and what only the service does

An attached terminal holds no history, no meter and no run. Every number it
paints comes from the service's projection or is named as unmeasured: a local
zero is a measurement of a conversation this process has never seen, and
`model: ?` next to a model the service has selected is the same mistake with a
question mark.

| Surface                             | Attached source                                          | What replaces the local value                                                 |
| ----------------------------------- | -------------------------------------------------------- | ----------------------------------------------------------------------------- |
| `alt+d` view and `/debug --text`    | `GET /api/debug`                                         | Full report measured by the service; unavailable values remain unknown        |
| Position (`seq`) of a typed message | the service's rows, re-read after it accepts the message | no position: the line is not clickable until a row exists                     |
| Sidebar subject line                | the session                                              | `session · <agent>`, never the open node                                      |
| Turn clock                          | this terminal's own interval while the turn runs         | — the elapsed time is real, because the turn started when the service said so |

**The debug view.** `DebugMixin` keeps its in-process path for local runs.
Attached, it reads `GET /api/debug` through `ServiceClient.debug()`. The typed
`DebugSnapshot` carries a session ID, a `DebugReport`, and optional turn timing
text. Both modes use the same styled tables and plain-text renderer. The report
restores the request timeline, per-request cache and costs, prompt stack,
per-turn context, history composition, instruction and tool overhead, and
maintenance diagnostics.

`DebugDiagnostics` reads the active run's messages when a turn is in flight, not
just the last persisted checkpoint. It snapshots mutable message envelopes and
parts on the owner loop, then projects and serializes the report in a worker
thread. Projection is read-only: it never generates a summary or calls a
provider. Messages, argument values, instruction bodies, tool schemas, and
summary text do not cross the wire. `DebugProjection` retains only metadata;
immutable compaction events have an explicit JSON serializer.

Reads are on demand, with no debug polling while the view is closed. A burst of
service events is coalesced for 150 ms; the exclusive `debug-service` worker
rejects responses for another session. Unchanged reports don't rebuild the Rich
tables. The service shares a single in-flight calculation and caches the last
report by session, event revision, request capture, model, and budget.
Cancelling one reader doesn't cancel shared work. Timing text is refreshed
separately from the cached report. `debug_changed` events at agent-node
boundaries also refresh requests that produced no visible output.

A failed or invalid read clears stale measurements and shows an explicit
`Debug unavailable` message, never a fabricated zero. Closing the view stops its
coalescing timer and cancels its client worker. Turn timing is measured in
`src/core/agents/turn_timing.py` and is included in `/debug --text`, as it was
for local runs; the service does not import the TUI.

**`seq` of a typed message.** In process the position is `len(_history)`: the
prompt is the next message, and knowing it up front is what makes the line
clickable before the turn ends. Attached, `_history` is empty by construction,
so that number was always `0` — and `0` is a position, the first rollback point,
which preselected the first turn on every click. `on_prompt_input_submitted`
passes `None` there instead and the line stays inert (`UserMessage.on_click`
returns early) until the transcript is re-read from `/api/session/durable`,
where each row's index is its real `seq` and the click works again.

**The sidebar's subject.** The service projects the model, the context budget,
the loaded skills and the plan of the _session_; it projects none of them per
node. `SidebarPanel.update_subject` names the session — `session · <agent>` —
while a subagent is open in an attached terminal, and the node on screen is
named by the node view and by the window title. In process the label still names
the subagent, because there the panel does read that node's own history, meter
and plan.

**The turn clock.** In process `run_turn` schedules a one-second interval that
repaints the elapsed time and stops it when the turn ends. Attached there is no
run, so nothing scheduled it and the spinner sat on `0s` for the whole turn.
`ActivityMixin.set_activity` now schedules `activity_tick` itself while
`_client` is set, and drops the interval when the turn stops. The in-process
path keeps the one `run_turn` owns, so the same second is never painted twice.

The tests for these four rules are in `tests/tui/test_debug_view.py` (the report
as a pure function) and `tests/tui/test_ui_client.py` (the repaint on an event,
the cleared answered set, the subject line, the missing position, the clock).

## The test seam, and why it is allowed to exist

`SigilApp` still accepts in-process collaborators — `agent=`, `store=`,
`session=`. That is a seam, not a fallback, and it is fenced three ways:

1. `client=` and `agent=`/`store=` are mutually exclusive; passing both raises.
2. `src/sigil.py` passes a client and nothing else, and imports no
   `Composition`. The production path has no agent to hand over even by mistake.
3. The seam is named as such in the constructor's docstring, so the next reader
   meets the decision instead of rediscovering it.

`TurnMixin.run_turn` — the in-process turn loop, with its checkpoints, steering
and abort — still exists behind that seam, because the ~40 widget tests that
mount `SigilApp` assert on it directly. It is a real duplication of the turn
loop that `src/server/runtime/session_runtime.py` already owns, and it is
scheduled for deletion: when those tests mount a client (a fixture that starts a
runtime, like `tests/tui/test_ui_client.py`), `run_turn` goes with them. Until
then, nothing may grow in it that both paths need — the moment a decision is
written twice is the moment the two disagree.

## What this cut does not cover

Three gaps, each stated as it is:

- **A rebuilt gap is a repaint, not a splice.** `report_stream_gap` announces
  the gap, re-reads the state and repaints the transcript from the durable rows.
  The events that never arrived are not recovered one by one, and the block
  being written is discarded on purpose: an unknown number of deltas are
  missing, so continuing that block would draw a sentence nobody said.
- **A stored tool call keeps exactly what the projection carries.** That is its
  name, its `call_id`, its `args` and the result the service presented, so a
  stored call and a live one paint the same. The exception is a _builtin_ call:
  `views._part_view` projects its name and its result text but no arguments, so
  a stored web search is painted without them.
- **No `duration_ms` for a stored call.** The absence is described under _Timing
  on stored calls_ above, and it is the same one a restarted or reconnected
  screen inherits: `tool_finished` carries the measurement, nothing else does.

## The tests

`tests/tui/test_ui_client.py` proves the wiring against the real application:
real `create_app`, real `ServerRuntime`, real routes, real hub. The one
substitution is `/events`, and it is explained where it happens:
`httpx.ASGITransport` collects a response body and returns when the application
finishes, so a stream that stays open for the life of a terminal cannot go
through it. `_ServiceTransport` therefore splits by route — every request
through ASGI, `/events` served from the runtime's own `EventHub` with
`Event.to_sse()` and `replay(after)` — so the bytes, the cursor and the sequence
numbers are the service's while the read side stays cuttable.

Covered: sending to the durable queue, streaming into the transcript, steering,
stopping with the two-press rule, a question answered over HTTP, an image with
its marker, the first cursor, and a cut stream that announces the gap, resumes
and re-reads the state. Nothing waits on a fixed sleep; every wait is for a row,
a rendered line or a request count.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
