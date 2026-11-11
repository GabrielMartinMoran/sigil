# How context works in Sigil

This page explains what Sigil sends to the model on every request, what it leaves
out, and how it measures the difference. Read it before changing the context
policy or debugging a turn that "forgot" what the agent did.

Three words carry the whole page, and they are [the domain](domain.md)'s: a
**turn** is the span from the user's message to the agent finishing, a **prompt**
is a slice of a turn, and a **step** is one model request.

The short version: **the session log keeps everything, but the model only ever
sees a projection of it.** Keeping the log intact is what makes the session
resumable; projecting it is what keeps the request inside the window. Ordinary
operation only appends, so the request prefix stays cacheable until Sigil
deliberately compacts.

## The two things that exist

There are two distinct message lists, and confusing them is the source of most
bugs in this area.

| | The log | The view |
| --- | --- | --- |
| What it is | Every message the run produced, in order | What goes to the model on the next request |
| Where it lives | `ctx.messages`, returned by `run.all_messages()` | `request_context.messages` |
| Persisted | Yes, to the session store | No |
| Modified by the view | Never | Yes, at a compaction boundary |

The capability that applies the view is **deliberately non-destructive**: it only
reassigns `request_context.messages`, a list owned by the request. It never rewrites original parts in `ctx.messages`. It adds projection records
and content-free maintenance receipts to message metadata, so a compacted session
still has the whole conversation on disk. Resume rebuilds the same projection.

## The three sections of a projection

A projection can carry three sections at the same time, and most of the design
follows from that.

1. **Checkpoint.** A structured summary, written by the session's own model, that
   replaces everything before its boundary. It is stored with the decision, so a
   resume replays it instead of paying for it again.
2. **Compacted history.** The exchanges after that boundary, with old reasoning
   removed and old tool payloads reduced to the form each tool declared.
3. **Live tail.** The protected recent work and everything new. It grows by
   appending and is never rewritten.

```text
[instructions and schemas][checkpoint][compacted history][live tail][agent context]
```

The final agent context contains the current agent relationships and notifications.
Sigil renders it for each request and omits it when empty. It travels as a
request-only user-role message, not as an actual user instruction or a persisted
conversation message. Its template identifies notifications as untrusted,
informational data. The provider may serialize or combine message parts differently.
The context projection and safety budget include this final block.

`CompactionPart` carries the checkpoint. Everything else is the original message,
sometimes with parts removed or reduced; protocol ids, tool names, outcomes and
order are never changed.

## When compaction happens

Two thresholds, both a share of the model's total projected input — instructions,
tool schemas and history together:

| Setting | Default | What it does |
| --- | --- | --- |
| `SIGIL_CONTEXT_TRIGGER_PERCENT` | `60` | Above it, one deterministic batch runs |
| `SIGIL_CONTEXT_TARGET_PERCENT` | `40` | Above it after that batch, a checkpoint is requested |

They are starting hypotheses, not measured quality boundaries. The trigger is
measured on the request as it would be sent *without* intervention, so a
compaction cannot hide the size that caused it.

The deterministic stage protects the most recent **five reasoning blocks** and
**twenty tool calls**, counted individually (a parallel call is one call, and a
response with several reasoning parts is several blocks). It reduces everything
older, in one batch, so a call changes form at most once.

Sigil also runs this deterministic stage when the resolved instructions change
from the previous completed request, even below the trigger. This uses a
content-free fingerprint stored with the request decision; the first request and
older records without a fingerprint establish a baseline without triggering a
batch. The change alone never requests a model-written checkpoint. Pressure can
still trigger one independently. Cache reuse depends on the provider and is not
guaranteed by this optimization.

The checkpoint stage runs only when the reduced projection is still above the
target, and only over a span that can advance: the boundary moves forward with
the protected tail, and a summary that would cover the same span again is not
requested.

The hard safety budget is separate. Fixed blocks, the larger of the configured
output reserve and the request's `max_tokens`, and a configurable uncertainty
margin must leave room for history. The runtime margin defaults to 5% of the
window; it is an uncalibrated allowance, not a correctness guarantee. Hard-budget
pressure also triggers compaction when it occurs below the percentage trigger.

Sigil reuses a provider input measurement only when the projected prefix and
model still match the recorded request. It adjusts for current fixed blocks,
new output, and newly appended messages. A changed prefix or model falls back to
local estimates. Retained opaque reasoning reserves at least its response's
reported output size when available; without usage, its size remains unknown.
Multimodal estimates and provider tokenizers can still differ.

An overfull request is refused with `ContextBudgetExceeded`, not made to fit by
dropping user history. An unknown window also blocks live append-only requests;
you must select a model with a known window. Pure diagnostic projections remain
available and report `window_unknown`. `passthrough` is an explicitly unguarded
diagnostic option, not a safe substitute for a known window.

## Checkpoint generation and accounting

A checkpoint update contains the previous checkpoint and only the newly eligible
span, in its deterministic compacted form. It never resends the entire raw prefix.
The summary adapter excludes persisted metadata, reasoning, signatures, and binary
payloads; it keeps attachment identifiers and explicit omission markers instead.

The complete auxiliary prompt must fit its own input budget before a network call
starts. Its output is capped at 4,096 tokens or a smaller reservation, and the
whole call has a 90-second timeout. A response must be complete, nonempty text
without tool calls. A rejected or cancelled response never becomes a checkpoint.
If generation fails, the deterministic view can continue only if it fits the
hard budget. Otherwise the request stops explicitly.

Content-free `sigil_maintenance` receipts retain auxiliary usage, model identity,
pricing information, and attempt status. They are stored on the owning transcript
envelope and deduplicated by attempt id after resume. Known usage is counted even
when a response is rejected or the following conversational request fails. A
cancelled or failed attempt with no returned usage is marked unmeasured, not free.
Session cost includes these calls; conversational cache timelines do not invent
an extra assistant step for them. Receipts follow transcript rollback boundaries,
so a rolled-back transcript is not a permanent provider billing ledger.

Provider-native exchanges remain intact. The summary boundary stops before the
first native exchange and reports `native_tools_preserved` under pressure. This
can prevent further summarization; if the protected projection cannot fit, Sigil
fails rather than silently rewriting a provider protocol.

## Per-tool compactors

Each tool this project owns declares what it keeps of its arguments and of its
result, next to its registration:

```python
@tools.tool(permission=_READ, describe=describe_read, compaction=READ_FILE_COMPACTOR)
```

There is exactly one compacted form, not a ladder. A call is either as it was or
reduced once, which is what makes the persisted decision a set of call
identities instead of a frontier per level.

A declaration must keep these rules:

- It is a pure function of the value: no IO, no clock, no model call.
- It never invents information and never expands a value; if the compacted form
  is not smaller, the original wins.
- Identity, status and errors survive. A failed call must never read like a
  successful one, so a failed result is shortened as text and keeps its outcome.
- User decisions are protected verbatim: a truncated negation changes authority.
- Opaque binary and multimodal payloads are left untouched and reported instead
  of being stringified into partial bytes.

A tool from outside this project (an MCP server, an external tool) has nobody to
decide what it keeps, so it gets `GENERIC_COMPACTOR`: a whole-value budget of 500
characters, with status and error metadata preserved even when that exceeds it.

`ToolCatalog.uncovered()` lists the tools registered without a declaration. Every
tool Sigil owns declares one, and the agent build refuses a non-empty answer.

## What a decision contains

`CompactionState` is content-free except for the checkpoint text:

| Field | Meaning |
| --- | --- |
| `dropped_parts` | Reasoning parts removed from the view, by stable part key |
| `compacted_calls` | Tool call ids sent in their compacted form |
| `summary` | The checkpoint text, or nothing |
| `summary_boundary` | Part key of the first message the checkpoint does not replace |
| `image_removals` | Stable occurrence keys and the original omission reason for removed images |
| `evaluated_steps` | How many steps the decision was taken at |

The record lives in the metadata of the message it preceded, so a rollback, a
fork and a SQLite transaction carry it with the response. Nothing in the log is
rewritten to store it.

Replay is deterministic: `recorded_state` reads the record, `plan` extends it
only under pressure, and `apply` rebuilds the view from the original parts. A
resumed session, an alternative branch and a failed request all land on the same
projection.

The current record is version 4. Non-tool identities include a content digest to
separate parts with equal timestamps. Version-3 keys and boundaries are translated
explicitly. Version-2 frontiers are expanded into call and part identities, then
use the current single compacted representation. This preserves the selected
calls, not the exact historical LOD payload bytes; migration can reset the cache.
Old records contain no durable image-removal decision to reconstruct. Unknown
versions raise `ContextPolicyVersionError`.

## Images

An image is priced by its pixels, not by its text. It stays attached for
`SIGIL_CONTEXT_IMAGE_ROUNDS` turns, and past that it is replaced by a note that
names the id `view_image` accepts.

Pixels expire **at a compaction boundary**, never on an ordinary request: the
rewrite costs the prefix, and an image left behind is one tool call away. The
pixel ceiling is a share of the window (`SIGIL_CONTEXT_IMAGE_MAX_PERCENT`), not
of the room left. Each removal and its reason are persisted by image occurrence,
so an image cannot reappear when pressure falls or a session resumes. Fetching it
again creates a new occurrence that can travel.

## Settings

Configure these values before startup. Retention counts accept zero and must be
non-negative; percentages require `0 < target < trigger <= 100`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SIGIL_CONTEXT_STRATEGY` | `append-only` | `append-only` or `passthrough` |
| `SIGIL_CONTEXT_TRIGGER_PERCENT` | `60` | Share of the window that starts a compaction |
| `SIGIL_CONTEXT_TARGET_PERCENT` | `40` | Share the deterministic batch should leave behind |
| `SIGIL_CONTEXT_RECENT_REASONING_BLOCKS` | `5` | Individual reasoning parts protected at a boundary |
| `SIGIL_CONTEXT_RECENT_TOOL_CALLS` | `20` | Individual tool calls protected at a boundary |
| `SIGIL_CONTEXT_SAFETY_MARGIN_PERCENT` | `5` | Window percentage reserved for uncertainty, from 0 to 99 |
| `SIGIL_CONTEXT_IMAGE_ROUNDS` | `3` | Turns an image stays attached |
| `SIGIL_CONTEXT_IMAGE_MAX_PERCENT` | `10` | Share of the window pixels may take |
| `SIGIL_CONTEXT_OUTPUT_RESERVE` | `8000` | Tokens reserved for the response |

`passthrough` sends the log as it is. It exists for diagnostics, and it never
compacts, so it can exceed the window.

## Reading the numbers

`/context` and the debug view separate what was measured from what was
estimated:

- **Measured** tokens come from the last provider response's usage.
- **Projected** tokens add an estimate for the messages after it.
- **Unknown** signed reasoning has a signature but no readable content; it is
  counted as unknown instead of being passed off as a measurement.

The maintenance block reports the reasoning blocks kept, the compacted call
count, the checkpoint size, the thresholds in effect, and any exception the
compactors raised. It never contains argument values, tool names, ids or
conversation text.

## Where the pieces live

Planning, provider I/O, and accounting have separate responsibilities:

| Path | What it does |
| --- | --- |
| `src/engine/context/capability.py` | Hooks the view into each request; asks for a checkpoint when the projection does |
| `src/engine/context/compaction/coordinator.py` | Pure plan: read the record, compact under pressure, apply |
| `src/engine/context/compaction/projection.py` | Prepare and finish a request; measures and reports |
| `src/engine/context/compaction/summary.py` | Pure checkpoint boundary and incremental span selection |
| `src/engine/context/compaction/summary_generator.py` | Text-only prompt, bounded provider call, response validation |
| `src/engine/context/compaction/records.py` | Record validation, legacy migration, replay |
| `src/engine/context/compaction/identity.py` | Shared stable transcript identities |
| `src/engine/context/compaction/maintenance.py` | Durable auxiliary-request receipts |
| `src/engine/context/input_estimate.py` | Same-model prefix validation and provider-plus-delta estimates |
| `src/engine/context/replay.py` | Offline deterministic-policy comparison, without model calls |
| `src/engine/context/compaction/events.py` | Persisted decision and events |
| `src/engine/context/strategy/append_only.py` | The default strategy |
| `src/engine/tooling/compaction.py` | `ToolCompactor` and the generic size policy |
| `src/engine/tooling/compaction_policies.py` | What each own tool keeps |
| `src/engine/tooling/compaction_constants.py` | The character budgets behind those declarations |

To change what a tool keeps, edit its declaration. To add a tool, declare a
compactor with its registration: the catalogue check fails otherwise.

## Limits worth knowing

- Dynamic instructions, `AGENTS.md` snapshots and skill activation can still
  invalidate an earlier prefix; they are outside this policy.
- Provider-specific additive tool loading is not implemented.
- A checkpoint is lossy by construction. It is compared against the transcript
  when quality matters, and it is never stored unless generation completed.
- The thresholds are not calibrated against measured model quality.

## Reproduce a local policy comparison

Export a Pydantic AI message-history JSON file, then run the offline comparison
from the repository root. Supply explicit window, fixed-input estimates, and
hypothetical per-million input prices:

```bash
uv run python -m scripts.context_replay transcript.json --window 128000 \
  --fixed-tokens 10000 --uncached-price 1.00 --cached-price 0.10
```

The command compares trigger percentages 60, 75, 85, and 95. It reports attempted
projections, estimated input tokens, matching complete-prefix parts, planned
maintenance, pending summaries, and whether replay stopped at an unsafe request.
The target is 60% except in the 60%-trigger case, where it is 45%. Blocked
projections count as attempts but contribute no hypothetical input cost.

This is not provider cache usage or total task cost. It makes no model calls,
produces no synthetic checkpoints, and excludes cache-write and auxiliary-request
prices. It cannot establish model quality or predict actions under a changed
context. Live weighted cache reads, actual total cost, latency, and checkpoint
fidelity remain necessary before calibrating the defaults.
