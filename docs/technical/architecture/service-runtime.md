# Durable server consumer (part A)

The web server owns its database through an exclusive OS lock in
`src/server/launch/server_ownership.py`. A second cooperating server fails at
startup with `OwnershipError` instead of serving. Ownership is taken before
reconciliation and held until the process closes; legacy processes that never
take the lock are outside this boundary.

On startup, `ServerRuntime.start` marks every non-terminal accepted input as
`interrupted` without executing it. A consumer task then claims `accepted` rows
in sequence order and runs them through the normal `agent.iter` path. Inputs
accepted while a turn is running are handed to `run.enqueue` at an existing
steering seam; a steer that misses that seam, including the end-of-turn window,
is left visibly stopped and is never promoted to a later turn. The consumer also
uses a bounded poll, so an input accepted by another runtime is picked up
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

Mid-turn recovery limit: a conflict during a throttled checkpoint saves the
local progress into a recovery copy and binds the UI to it, while the running
turn keeps its local session reference. The final save reconciles again and
binds the winning session. A second conflicting write in that window can
therefore leave more than one recovery copy; no transcript or delivery fact is
merged across them. See `SessionRuntime._checkpoint` and `SessionRuntime._turn`.

Abort and consumer death: abort sets `_aborting` so `_pump_steering` stops
claiming new work and `stop_open(keep=...)` does not stop requests already
handed to `run.enqueue`. Once the run unwinds, requests confirmed from its
snapshot become `finished`; the rest become `stopped`. A consumer that dies
calls the session-scoped `interrupt_open`, so only that session's accepted rows
become `interrupted` and its `send` refuses work nobody will consume. Process
startup still reconciles open rows database-wide.

## Process ownership and session ownership

**Scope:** server runtime ownership. **Owner:** server. **Last update:**
2026-11-15.

The service has two lifetimes, and the code names them. `ServerRuntime` in
`src/server/runtime/server_runtime.py` is the process: it owns the database lock
(`src/server/launch/server_ownership.py`), the shared accepted-inputs handle,
the `EventHub` it publishes on, the reconciliation `start` performs and the
service-wide idle decision. `SessionRuntime` in
`src/server/runtime/session_runtime.py` is one session: the turn loop, the
consumer that claims its rows, the questions it waits on, and the callbacks the
engine calls — tool log, model retry, question hub and tree.

The facade passes what a session needs — the composition, the hub, the shared
accepted inputs, the resolved question deadline and a typed worker-ownership
guard — and takes no way back: `session_runtime.py` never imports
`src/server/runtime/server_runtime.py`. The constants, `PendingQuestion`,
`IncomingImage`, `decode_images` and `resolve_question_timeout` live in
`src/server/runtime/runtime_support.py`, which both sides import; `runtime.py`
re-exports them, so the routes, the CLI and the tests that already name them
there need no change.

Closing is asymmetric on purpose. `SessionRuntime.close` resolves its question,
cancels its consumer, and detaches its seven callback registrations; it never
releases the process lock or closes the shared cached database. Its asynchronous
`aclose` waits for the consumer, cancelled subagents, background title tasks,
and deferred MCP startup before closing the gateway. The service awaits every
resident session and its extension cleanup before releasing ownership. Cleanup
continues through errors; cancelling the caller does not abandon an in-progress
MCP loader.

`ServerRuntime` holds a registry keyed by session ID. Each entry owns a distinct
composition, model router and active selection, context meter, MCP gateway,
roadmap stores, agent tree, consumer, and pending question. Provider
credentials, installation preferences, the database, and the process lock remain
shared. Changing one session's model updates its own active selection; the
shared last selection is only a fallback for future conversations.

A client selects a session, not the service. Creating or opening another session
returns its ID without rebinding another live composition. The client includes
`session_id` on subsequent reads, actions, and `/events` subscriptions. HTTP
also accepts the `X-Sigil-Session` header; the explicit query parameter takes
precedence. Missing selectors return 422 and nonresident IDs return 404. The
exceptions are process-level health, authentication, static assets, and the
initial unscoped `GET /api/state` bootstrap, which returns an available default
ID. The open route uses its body ID to load a persisted or resident session.
Session IDs are routing identities, not additional authorization credentials.

Session callbacks label events with internal ownership metadata. SSE filters
both live events and replay while retaining the global sequence and service-wide
events. TUI and web replace their streams on selection changes and reject stale
responses. Switching away does not stop a turn or a background child: its report
wakes its original session's consumer, even with no client watching.

Requests and SSE connections lease their selected entry for their complete
response lifetime. Idle entries have a separate grace period, configured by
`--session-idle-grace SECONDS` (default 300). The reaper releases only entries
without a client/request lease, active turn, queued input, running child, or
unread child report. Releasing an entry closes its resources without touching
another session or the process lock. A persisted session can be opened again; an
empty draft remains in memory only and disappears when released. The process
idle timer below remains independent.

## Idle shutdown

A service nobody needs should not outlive the work, so the server stops itself
instead of waiting to be killed. `IdleShutdown` in
`src/server/runtime/server_runtime.py` asks two questions, in this order:

- **Is work running in any session?** The registry checks every resident turn
  and its background children. A pending question needs no rule of its own: the
  agent asks from inside the turn that is waiting for the answer, so the turn is
  still running and the question is covered. There are no interactive approvals
  (see [Risks](../design/core-boundary.md#risks)).
- **Is any row still open?** `AcceptedInputs.has_open`, which is database-wide:
  `accepted`, `claimed` or `delivered`, in _any_ session, because one service
  serves the whole database. A row another browser accepted is work this process
  must not stop in the middle of.

The service stays running without an idle deadline by default. When explicitly
set, `--idle-grace` enables shutdown after the specified number of seconds
without work. Connected event subscribers do not count as work for this optional
idle policy.

While work exists the service stays up, however much time passes. When it does
not, the configured window starts. One poll that sees work drops the window and
starts it again later, so a burst of short turns never adds up to a shutdown.
The poll is `IDLE_POLL_INTERVAL` (0.5 s), and the question it asks is one lookup
rather than a scan: `AcceptedInputs.has_open` is a single statement over
`accepted_inputs_open`, the partial index this cut added over exactly the three
open states (see [the storage page](storage.md#schema-steps) for the measurement
that motivated it). A work check that raises is not proof of idleness: the watch
keeps serving and says so once per streak, because a service that dies of a
store hiccup is worse than an extra window.

The stop itself is graceful and does nothing else.
`ServerRuntime.stop_when_idle` calls the one request it was given —
`GracefulStop.request`, bound in `src/server/cli.py` to uvicorn's own
`should_exit`. The process is never killed and the endpoint is never removed by
hand: uvicorn leaves its loop, the lifespan shutdown runs in its normal order
(watch cancelled, endpoint unpublished by `EndpointPublisher.clear`, runtime
closed, ownership lock released), and the next client that asks for a service
finds the store free and launches a new one.

Idle shutdown never relabels a row at the moment it leaves. A request can be
accepted between the last idle look and the loop leaving, and
`ServerRuntime.close` releases the lock and closes the composition without
touching the queue, so that row keeps the state it has (`accepted`) instead of
being written as executed, delivered or interrupted. That is the honesty this
path owes: the exit reports what it did, which is nothing to that row.

It does not save the row from the label. The row was never executed, and the
next start's reconciliation (`ServerRuntime.start`) marks every non-terminal row
`interrupted` — correctly, because that is what happened to it. What the exit
avoids is the _false_ claim, made by the process itself while it was still
serving, that the row was lost to a crash; the label that follows is a statement
about execution, and only reconciliation is entitled to make it.

Durable read (B2): `GET /api/session/durable` calls `views.durable_session`,
which opens one SQLite read transaction and returns the messages rows as the
transcript, the accepted-input queue with state and sequence, `input_deliveries`
facts, recovery links with availability, a `pending` overlay, and
`conflicted_entries`. Pending means accepted or claimed without a delivery fact;
delivered/finished rows never reappear there and are read from the transcript. A
deleted recovery copy is reported unavailable without a broken link, and
conflicted entries are kept separate from the current transcript. Live deltas
are not mixed into this read. A session that exists only in memory has no row to
project, and the read answers that with a typed `404` — "nothing yet", not a
failure. The web client treats it as the normal state it is: it clears the
overlay instead of leaving the previous conversation's queue painted over an
empty one, and says nothing about it.

## A turn that is waiting says so

A turn can be slow without being broken, and the service used to be unable to
tell you the difference. The engine already retried transient model failures —
five attempts with a backoff of 0.5 to 8 seconds, see `src/core/agents/retry.py`
— but only the in-process client listened for the notice:
`SigilApp.notify_model_retry` paints every attempt in its chat. In the service
that callback had no subscriber, so a provider that kept failing published
nothing at all and the screen read "Thinking" for minutes. `ServerRuntime` now
subscribes `RetryTransientModelErrors.on_retry` and publishes what the terminal
would have painted.

The event is the one both clients already know: `notice` with `tone` set to
`warn`. Its text is the terminal's own sentence, so the two surfaces say the
same thing about the same wait — the attempt that failed, the ceiling, the
reason and the delay, as in
`↻ Transient model failure (HTTP 502 · …); retry 2/5 in 0.8s`. Nothing new is
asked of a client: the web store and the attached terminal already paint a
`notice`, and the sink is wrapped so that a client which cannot be told is not a
reason to fail the retry it describes.

Silence gets a sentence too, and never a deadline. `SLOW_TURN_NOTICE_SECONDS`
(60) is how long a turn may publish nothing before
`SessionRuntime._watch_turn_silence` says it is still waiting. The watcher is a
task of the turn, cancelled with it, which waits on an event every published
event sets: a turn that is streaming, or retrying, never reaches the notice, and
a turn that ends before the window passes never says it. It fires at most once
per turn and it cancels nothing. That is the decision this cut records: **there
is no turn deadline**. A model that takes minutes is legitimate, `agent.iter`
runs with `UsageLimits(request_limit=None)`, and only an explicit stop, the
question deadline, or the step budget ends a turn early.

A turn that ends in error closes its rows, exactly as a cancelled one does.
`SessionRuntime._turn` calls `_stop_pending` when the status is `error`, so the
accepted input cannot stay `claimed` until the next start interrupts it — which
is what used to happen, and what made a failed turn look like a lost one. The
terminal state is `stopped`, not a new one: `_TRANSITIONS` in
`src/core/storage/accepted_inputs.py` allows `claimed → stopped` and has no
failure state, while `interrupted` stays reserved for reconciling a process that
died, as [idle shutdown](#idle-shutdown) states.

## The transcript, one window at a time

`GET /api/session/transcript` answers a conversation that grows, so it takes a
window instead of always sending the whole thing:

| Parameter | Meaning                                                              |
| --------- | -------------------------------------------------------------------- |
| `limit`   | how many items to return; absent means the whole transcript          |
| `before`  | the position the page ends just before; absent means the newest page |

The reply carries `items`, `first_index` — the absolute position of the first
item of the page — `has_more`, whether anything older exists, `total`, the
number of items in the whole transcript, and `pending`, the rows waiting in the
queue. Those are the only fields paging adds; the items themselves are the
projection `views.transcript` already defined.

Three rules make the cursor usable:

- **Positions, not message indexes.** The `index` of an item is the message it
  was built from, and several items can share one — a message with a thought and
  a tool call. A cursor expressed in message indexes could not be chained
  without repeating a message or dropping part of it, so `before` and
  `first_index` are positions in the flat list. Asking with `before=first_index`
  returns exactly the chunk above the page: no hole, no repeat.
- **An item keeps its index.** A page never renumbers an item, because a click
  on a message maps its `index` to a rollback point. A tramo that renumbered
  would send the reader to the wrong turn.
- **A queued message is not history.** The rows waiting in the queue travel with
  the newest page only — the read asked for without `before`. They are not
  items, they do not count in `total`, and they never move a cursor. An older
  page answers with an empty `pending`: a row that is not history has no place
  in a chunk, and sending it with every page would paint it more than once.

The wait is one rule and not two. `views.waiting` decides which rows still wait
— accepted or claimed, and never delivered — and both reads that publish the
queue call it: `views.pending_rows` for the newest page and
`views.durable_session` for [the durable read](#durable-server-consumer-part-a).
A screen and a recovering consumer therefore cannot be told different things
about one waiting message.

Compatibility is deliberate: without `limit` the answer is the whole transcript
with `first_index` 0 and `has_more` false, which is what a client that has not
migrated still gets. The window size — 100 items — is the client's decision to
send, not a default the service imposes. An unusable `limit` or `before` is a
`400` with a sentence: a limit that is not a positive whole number or is above
`MAX_TRANSCRIPT_PAGE` (1000), a cursor that is not a non-negative whole number.

Implementation: `views.transcript_page` is the only place the rule lives — it
takes the items already built and returns a `TranscriptPage`; the route
validates the two parameters and projects. See
`tests/tui/test_server_runtime.py` for the arithmetic and
`tests/server/test_server_parity.py` for the query contract.

`views.transcript_window` projects the window from the messages — it counts each
message's parts and builds only the ones the page returns — so a page costs what
it holds, not what the session has; `views.transcript_page` stays as the oracle
in tests.

The terminal is the first client to use it. `ChatLog` keeps at most
`MAX_MOUNTED_PAGES` (3) pages mounted. It asks for the newest page with
`limit=transcript_window` — 100 items by default, the `transcript_window`
preference in `ui.json` — and when the reader reaches the top it asks for the
page above with `before=` the absolute position its oldest mounted page starts
at. That insertion lands above the viewport, so the scroll is moved by exactly
the height it added: the widget is mounted with `mount(before=0)`, and
`call_after_refresh` then puts the row the reader was looking at back on its
screen row with `scroll_to(immediate=True)`. What they were reading does not
move.

The window counts every page twice and keeps both counts. A page records the
widgets it mounted, the items it stands for, and the `first_index` the service
reported — `ChatLog`'s `_Page`. The **cursor moves in items**: the window starts
where its oldest page starts, so letting that page go leaves the next page's
recorded start as the cursor, never the number of widgets the page happened to
mount. The **distance to the screen is measured in widgets, on purpose**:
eviction asks how much of the screen lies between the reader and a page, and a
page that merges a thought and its answer into one block, or a call and its
result into one row, holds fewer widgets than the items it stands for — the
viewport, not the transcript, is what must not move. Conflating the two was a
real defect: a page of 100 items that mounted 60 widgets moved the cursor 60
positions instead of 100, so the next ask ended 40 items short and those
messages were never painted. `tests/tui/test_chat_history_render.py` proves the
two counts apart on a page of 100 items painted as 60 widgets;
`tests/tui/test_ui_client.py` proves a session whose projection is not one item
per widget loses no item when the page at the top is let go.

Past the bound the page furthest from the reading position is let go, not the
page held longest: what has to stay is what the screen is showing. The position
is taken before the page above is inserted — the insertion moves the scroll
offset, not the row the reader is on — and turned into a position among the
mounted widgets, which is the unit of that measurement: a tool block is not as
tall as a line of text, and a page's widget count is not its item count. It
gives two outcomes, one for each way a reader arrives at the bound. A reader at
the top is furthest from the newest page, so that one leaves and the window
stops ending where the conversation ends. A reader who came back down is
furthest from the page at the top, which is the page the cursor can ask for
again. Either way the page that leaves is the one the viewport shows least of,
so nothing on screen moves. The block being streamed into and the queue's
waiting lines belong to no page and are never destroyed; a page that somehow
held one is skipped, and the window grows past its bound rather than losing what
is live. A waiting line stops being live as soon as the app retires it — the run
read it, or its row stopped being pending — and `ChatLog.retire_pending` is what
says so: `_is_live` reads that list, and a line left in it after it was taken
off the screen would keep a removed widget alive while vouching for nothing that
is painted.

What leaves above is asked for again with the cursor it carried, so history is
never lost — only unmounted. What leaves below cannot be: the route pages
backwards only, and there is no `after` cursor to walk down with. So a window
that no longer ends where the conversation ends — `_reaches_end` false in
`ChatLog`, `reachesEnd` on the web — reads the newest page again, the same read
the screen opened with, as soon as the reader reaches the bottom. Once per
arrival: while that read is in flight, and after it lands, the window ends the
conversation again and a reader who keeps scrolling at the bottom asks for
nothing more. The anchor goes back to the bottom with it, because the bottom is
what they asked for. See `tests/tui/test_ui_client.py` for the ten cases: the
window a long session mounts, the cursor a scroll asks for, the row that does
not move, the second page, the bound and the live block, the newest page a
reader at the top lets go of, the newest page read again at the bottom, the page
at the top a reader near the bottom gives up, the click that still names its
turn, and the window preference that falls back and clamps.

The web client is the second, and it holds the same bound with a different
mechanism. `web/src/transcriptWindow.ts` is the whole rule, as pure functions
over plain data: `newestWindow` makes the newest page the window,
`withOlderPage` prepends the page above with its cursor, `reachesEnd` answers
whether the bottom of the window is still the end of the conversation, and
`wantsOlderPage` and `wantsNewestWindow` decide when a scroll event has earned a
read. It is pure so the rule is testable without a store, a request or a DOM —
`web/src/transcriptWindow.test.ts` pins both eviction directions and both
triggers. The store keeps the window in `items` plus the `firstIndex` cursor,
the page sizes, the `total` and the reader's position, and
`web/src/Transcript.tsx` only reports what only the DOM knows: where the scroll
is, and when it reached an edge. The size is `TRANSCRIPT_WINDOW` (100),
mirroring `DEFAULT_TRANSCRIPT_WINDOW`, because the web client has no settings
surface of its own.

That reported position has one unit: a fraction of the scroll, 0 at the top of
the window and 1 at the bottom, held in the store as `readingFraction`. It is
not an item, because what the component measures is pixels and what the eviction
is counted in is items — two units with no conversion is the defect this
replaced: a position out of a thousand was handed to `withOlderPage` as an
index, so a reader below the first tenth of a 300-item window read as "at the
bottom" and the page being read was the page let go of.
`readingIndex(fraction, itemCount)` in `transcriptWindow.ts` is the only place
the two meet, which is why the mismatch cannot come back silently. The component
reports on every scroll event, before the read that event may ask for, and again
after restoring the scroll offset when a page lands above — the reader is where
the insertion left them, not where they were before it.

The reading position is what an eviction is measured against, and that is the
difference from a window that simply keeps the newest page. `withOlderPage`
computes the distance from the reader to the nearest edge of each mounted page
and lets go of the furthest one — the page held longest when the reader is at
the top, and the page at the top when they have come back down and kept reading
lower. Ties go to the front, because the top of the window is the side a cursor
can walk back into. The streaming block and the queue's waiting rows belong to
no page: they are painted from the store's `stream` and from the durable read,
never from `items`, so a page that leaves cannot take either with it.

What leaves the bottom has to come back a different way, because the route pages
backwards only: there is no `after` cursor. So when the reader reaches the
bottom of a window that no longer ends at the end of the conversation —
`reachesEnd` false — the client asks for the newest window again with the same
read the screen opened with, `refetchTranscript({ keepStream: true })`, rather
than inventing a forward cursor. `keepStream` is explicit because that read can
land in the middle of a turn: the deltas already received are the answer being
written, and dropping them would blank the block until the next one arrived.
Only the reload that runs where the turn has ended — `turn_finished`, a lagged
stream, an opened session — clears the stream, because there the transcript is
the record. Between the two edges the screen holds at most `MAX_MOUNTED_PAGES`
(3) pages: at most 300 items of a conversation that may hold thousands, and no
read of the whole thing at any point.

An insertion above the viewport moves the scroll by exactly the height it added:
the component records `scrollHeight` and `scrollTop` before asking and, once the
window has grown, restores `scrollHeight - (height - top)` — the same row on the
same screen line. What a reader was reading does not move, and the auto-scroll
that follows a streaming turn is only applied to a reader who was already at the
bottom. The scroll wiring itself is not automated: this repository has no DOM
test environment, so `Transcript.tsx` is covered by the pure predicates it calls
and by review, and the comparison is made by hand in a browser.

## The waiting overlay is read cheaply, and off the loop

A queued message is announced by an event that carries no text, so a screen that
did not type it learns it from the rows. That read happens on every
`state_changed` with `reason=queue`, which is once per announcement during a
turn — the moment the service can least afford to be busy.

Two facts made it expensive. `GET /api/session/durable` projects the whole
conversation: 231 ms on a session of 1846 messages, for a screen that only
paints the waiting rows. And it ran on the event loop, so the stream that
carries the deltas waited behind it.

The overlay now reads `GET /api/session/pending`, which answers
`{'pending': [...]}` — the same rows, the same `views.waiting` rule, without the
projection (0.46 ms on the same session). The durable read keeps its own
callers: the web client still takes `queue`, `conflicted_entries` and
`recoveries` from it for its recovery and conflict panels.

Off the loop is a rule, not a one-off. `views.durable_session` and the unbounded
transcript projection run under `asyncio.to_thread`, with their arguments
resolved on the loop first: the database is built for it (`threading.RLock`,
`check_same_thread=False`) and the response is assembled from what the thread
returns. `GET /api/state` answers while a projection is still blocked in a
thread, which is what `tests/tui/test_server_runtime.py` asserts.

The terminal reads it in a worker, and a read in flight absorbs the burst: five
announcements that arrive together cost one read and at most one more, not five.
The handler returns as soon as the work is scheduled, so no delta waits for it.

## The turn command

`POST /api/turn` is the one way a message enters a session, and its shape is the
contract a retry depends on: `text`, an optional caller-generated `request_id`,
and `images`, a list of `{media_type, data}` where `data` is base64. The
position of an image in that list **is** its `[Image #n]` number in the text, so
the order is part of the message's identity. A client sends picture bytes; there
is no filename field and no path is ever opened, because a name in a JSON body
would be a request to read a file on the server.

`request_id` is the durable identity. The same id with the same source payload —
the same text and the same images as the client sent them — is the same message:
the response repeats `duplicate: true`, the same persisted image identifiers,
and nothing executes twice. The same id with a different payload is a conflict
and is refused. The identifiers in the response are the ones stored, never
regenerated, so a client can retry without losing track of images it already
sent.

The fingerprint covers the accepted text and the normalized image bytes and
media types, because `prepare_image` re-encodes every payload before it is
stored. That normalization is deterministic for a given input, so a retry of the
same original payload is idempotent, but it is not a fixed point for JPEG:
sending back bytes the server already normalized can produce different bytes and
is refused as a conflict, not recognized as a duplicate. The ids and timestamps
the server assigns are excluded from the fingerprint. See
`src/core/storage/accepted_inputs.py`.

Validation happens in `decode_images` (`src/server/runtime/runtime_support.py`)
before the queue is touched, so a refusal writes nothing:

| Refused         | Limit                                                                          |
| --------------- | ------------------------------------------------------------------------------ |
| Too many images | 10 per message                                                                 |
| Image too large | 4 MiB per image, checked after base64 decoding and before the image is decoded |
| Media type      | only `image/png`, `image/jpeg`, `image/gif`, `image/webp`                      |
| Payload         | must be valid base64 and must decode as an image                               |
| Request body    | 64 MiB of JSON                                                                 |

Images are re-encoded by `src/core/attachments/images.py` before they are
stored, which bounds their dimensions and makes the stored media type the one
the bytes actually are.

The browser still sends text and a request id only; the image picker is not part
of this slice. `BodyLimitMiddleware` is registered inside `AuthMiddleware`, so
an unauthenticated request is answered without its body being read. For an
authenticated one, a declared `Content-Length` is refused before the body is
read, and a chunked body is counted as it arrives; either way the `413` is sent
without the route's parser ever seeing a complete body.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
