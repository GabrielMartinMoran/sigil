# Context and cache optimization design

This historical record preserves an earlier context-optimization design,
exploratory measurements, audit corrections, and acceptance criteria. It does
not define the current defaults. Read the
[current context reference](../internals/context.md) before configuring or
changing the active policy.

<!-- prettier-ignore -->
> [!IMPORTANT]
> The implementation status, module paths, test counts, and proposed settings
> below describe the original investigation. The current append-only policy
> supersedes parts of this multi-level design. Local tests did not establish
> billed savings or equivalent task quality with a live provider.

At the recorded checkpoint, audit corrections were implemented and verified
locally. Calibration and representative task-quality evaluation remained open.
The audit found that the first implementation didn't match the agreement: it
used a moving per-turn window, synthesized periodic events, and lost real fields
in several level-of-detail (LOD) policies. Those behaviors weren't accepted
choices. Regressions then used real model and agent objects, SQLite, and
adapters with simulated transports; the previous suite alone hadn't validated
the requirements.

## What was implemented at that checkpoint

The revised path consisted of these components. Paths in this table are
historical, not a current source-file inventory:

| Component    | Location                                                      | Responsibility                                                                                                                                                   |
| ------------ | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Step clock   | `context/compaction/clock.py`                                 | A step is one model response, derived from the transcript so it survives resume, rollback, and fork                                                              |
| Event        | `context/compaction/events.py`                                | Immutable event and versioned boundaries: step, reason, unmet targets, and content-free counters                                                                 |
| Rules        | `context/compaction/policy.py`                                | Inclusive ceilings and targets; age determines eligibility, not immediate application                                                                            |
| Coordinator  | `context/compaction/coordinator.py`, `projection.py`          | Cross-turn hysteresis, batched LOD, and shared budget adjustment; the hook blocks requests that don't fit                                                        |
| LOD          | `tooling/lod.py`                                              | Enum, pure functions for each side, and a generic external-tool policy                                                                                           |
| Declarations | `tooling/lod_policies.py`                                     | Input and output policy for each built-in tool; a missing declaration fails the build                                                                            |
| Strategies   | `context/strategy/windowed.py`, `compacting.py`               | Use the coordinator and report events through `ViewResult`                                                                                                       |
| Debug        | `context/meter/debug*.py`, `ui/widgets/debug_view.py`         | Per-turn context, cache, and maintenance-event table                                                                                                             |
| Skills       | `skills/tools.py`, `skills/loaded.py`, `skills/rendering.py`  | Reads return a reference; text travels in the dynamic block; binaries aren't sent there                                                                          |
| Persistence  | `context/capability.py`, `sigil_context` metadata             | Version 2, effective policy and boundaries in the transcript transaction, per-part rollback anchors, recovery before envelope merging, and request deduplication |
| Constants    | `context/compaction/constants.py`, `tooling/lod_constants.py` | Ceilings, targets, ages, and semantic/generic sizes with explicit units                                                                                          |
| Simulator    | `context/compaction/simulator.py`                             | Consistent read-only SQLite access, per-request accumulation, and hypothetical pricing scenarios                                                                 |

The recorded implementation removed the keep-all and keep-none reasoning modes
(`off`, `all`), together with `SIGIL_CONTEXT_REASONING` and
`SIGIL_CONTEXT_KEEP_THINKING`. At that stage, the step window was the sole
reasoning policy. This isn't a list of environment settings to use today.

## Recorded verification of the corrections

The complete suite at that checkpoint contained 1,976 tests. Sequential batches
and focused runs passed, including contracts for 23 built-in tools, aggregate
generic sizes, hysteresis, persistence, transactional failures, rollback,
concurrency, streaming, and partial resources across repeated resumes. OpenAI
Chat, Responses, and Anthropic adapters were checked with simulated transports.

Focused line and branch coverage was 93%. Python `ruff check`,
`ruff format --check`, and `git diff --check` passed. No type checker was
configured. Existing rendering and PAS messaging tests were load-sensitive and
passed in isolation without changes to their code. No live provider requests
were made and no billed savings were measured.

## What remained unproven

Local verification wasn't a substitute for calibration with real tasks and
providers. The remaining work was to:

- Measure per-task cost with representative sessions and provider prices.
- Calibrate ceilings and age boundaries using all accumulated requests, not the
  apparent saving from one maintenance event.
- Expand compacted-resource and repeated-resume checks with representative real
  sessions; a synthetic regression already covered that path.
- Evaluate long-task quality: which decisions degrade when tool detail is
  reduced.

## Goal and success criteria

Minimize cost and latency per completed task while retaining quality,
continuity, safety, and sufficient model-window headroom. A cache hit is useful
evidence, not a reason to retain irrelevant context indefinitely.

The primary comparison is total task cost: normal input, cache reads and writes,
output, summaries, rereads, and retries. Also compare latency, quality, peak
window use, prefix changes, and information-recovery frequency.

Compare the accumulated cost of keeping content with the cost of reducing it.
Keeping content resends it in later requests; reducing it can cause a new cache
write or miss and may require retrieval. Category prices and cache rules depend
on the provider.

## Agreed scope and decisions

The original agreement required these behaviors:

- Preserve persisted history and build a separate model view.
- Introduce coordinated context-maintenance compaction events, distinct from
  compaction that creates a conversation summary.
- Give each component an operational target `x`, a ceiling `y`, and separate
  hard safety limits. Reaching one ceiling batches all eligible component
  reductions.
- Use continuous agent steps across turns. Count a step identically in runtime,
  replay, debug, and calibration; parallel tool calls aren't separate steps.
  Distinguish the clock from the count of responses containing reasoning.
- Keep recent reasoning across turns, including user-question boundaries. The
  initial proposal used a 20-reasoning-step ceiling and 10-step target, plus a
  measurable 10% window ceiling and 5% target. Either ceiling triggers; both
  applicable targets must be satisfied afterward. Budget safety takes
  precedence.
- Define the full representation plus LOD 1, 2, and 3 for tools. Generic
  external targets were 2,000, 500, and 100 characters. Built-ins required
  semantic argument/result policies rather than those universal sizes.
- Keep skill instructions and successfully read text resources in that skill's
  dynamic section. Reread from disk when rebuilding the prompt; don't snapshot
  them. Script output remains ordinary history.
- Show content-free debug analytics and retain dynamic-block highlighting.

## Historical baseline before the corrections

This section records the starting point, not current behavior. The original
working tree contained prior uncommitted changes that had to be preserved; their
presence wasn't proof of review or completion.

### Context and cache

`WindowedStrategy` in `src/engine/context/strategy/windowed.py` used a moving
reasoning window from the current turn. The default was `recent`, with 10 steps
and an estimated 5% window ceiling. Older-turn tool results used a
4,000-character threshold and a 2,000-character maximum truncated result,
including the truncation notice.

`ContextViewCapability` projected history without modifying the log. `project()`
served debug and had to remain read-only. `CompactingStrategy` already had
distinct semantics; the coordinator wasn't permitted to break it or implicitly
become summary compaction.

Debug separated cache reads from writes, used tables, and omitted conversation
content. `OpenRouterBuilder` enabled cache breakpoints for `anthropic/` models;
that change was tested with a simulated transport, not a live provider.

### Skills

`src/engine/skills/loaded.py` and `src/engine/skills/rendering.py` contained a
partial implementation deriving resource reads from the transcript and rereading
files while rendering the skill block. One simple file-change and rollback test
existed. This wasn't complete: original results remained protected in history
and could duplicate content. Binary, partial-read, failure, compaction, resume,
and execution-isolation coverage was missing.

The automatic skill catalog remained sorted by name and `list_skills` existed.
Removing the catalog wasn't agreed: its cost needed comparison with the cost and
reliability of tool-only discovery.

## Collected evidence and measurement limits

These exploratory observations informed the design. They don't establish
provider billing or causal cache behavior.

### Reference session

A read-only SQLite snapshot through sequence 115 contained 56 measured
responses, 12,872,654 input tokens, and 5,521,664 cache-read tokens: 42.89%
aggregate. The recorded model was `gpt-6.1-terra` and provider `openai-chatgpt`.
Cache-write counters were zero; that didn't prove no cache writes occurred.

A local reconstruction using the OpenAI Responses adapter compared 55
transitions. Under simulated `recent`, 31 were append-only, 22 first differed in
reasoning, and 2 in a tool result. Aggregated actual cache-read rates for those
groups were 51.5%, 36.8%, and 0%. These were correlations, not causation.

The complete historical request wasn't stored: historical tool schemas,
effective parameters, and sometimes model-window data were missing. The
reconstruction therefore couldn't establish byte-for-byte equality or predict
real hits.

### Encrypted reasoning

The reference session contained `ThinkingPart` blocks with empty text and an
encrypted signature. An adapter can serialize them even when a content-based
heuristic estimates zero tokens. Zero estimated text isn't zero cost; encrypted
bytes must not be converted directly into billed tokens without evidence.
Count-based limits must work when size is unknown, and debug must distinguish
unknown from zero.

### Simulated LOD and visible reasoning

Seven saved executions ranged from 38 to 522 responses. In the initial
522-response simulation, fixed intervals of 10, 15, and 20 steps produced 51,
34, and 26 requests with changes to earlier results, respectively. These weren't
measured cache misses.

The visible-reasoning simulation used 163 blocks and 262,762 characters.
Periodic maintenance, retaining the latest 10 reasoning steps and approximating
tool LOD, produced these event averages:

| Interval | Events | History before | History after | Tool reduction | Reasoning reduction |
| -------- | -----: | -------------: | ------------: | -------------: | ------------------: |
| 10 steps |     52 |       ~110,772 |      ~104,486 |         ~5,110 |              ~1,177 |
| 15 steps |     34 |       ~113,928 |      ~104,350 |         ~7,778 |              ~1,800 |
| 20 steps |     26 |       ~116,450 |      ~103,878 |        ~10,219 |              ~2,353 |

Values are estimated history tokens. They average events at different positions,
exclude schemas and fixed instructions, and don't measure accumulated context
across every request. The simulation used fixed intervals, not the agreed
combined-trigger coordinator.

With 1,000 synthetic tokens per reasoning-bearing response, interval 20 gave
~126,183 tokens before and ~110,118 after, with ~5,846 mean reasoning reduction.
That was an artificial scenario, not an encrypted-token measurement.

### Tool arguments and results

In the 522-response execution, arguments totaled ~142,373 estimated tokens and
results ~284,134. Simulating full-history resend without pruning accumulated
~38.4 million and ~88.7 million tokens, respectively. These weren't billed
tokens and didn't subtract cache reads.

The reference session contained one anomalous call with ~40,456 estimated
argument tokens. That supported argument-side LOD and preventing exceptional
payloads at the source; it didn't establish that arguments outweigh results in
every session.

## Coordinator design

The proposed coordinator batches eligible maintenance while keeping the
persisted transcript intact.

### State and event model

Define a versioned per-execution event with stable step identifiers and
boundaries for reasoning and every LOD. Record the reason, effective policy,
before/after values, and unmet targets. Don't persist sensitive content or
reversible hashes. Reapplying an event must be idempotent.

Test the SQLite, transaction, and lifecycle integration. Child executions own
independent state. Resume reconstructs the same view; rollback discards or
invalidates later events. An alternative branch mustn't inherit decisions from a
discarded branch. Reading debug doesn't write events.

Cover old sessions, migrations, partial failures, concurrency, and configuration
or model changes. Don't silently recalculate an old boundary with new
parameters. Document the policy version and migration or state-reset rule.

### Triggers and coordination

Apply the following decision sequence:

1. Calculate each component's eligible-view state without mutating the
   transcript.
2. If any component reaches its operational ceiling, open a normal event and
   bring every component above its target toward that target.
3. If global budget pressure requires earlier action, open a budget event
   without waiting for ordinary ceilings.
4. If a request doesn't fit, apply the emergency path. If that isn't enough,
   escalate to existing compaction or report an error; operational minima can't
   override the physical window.
5. Record when protected sections or indivisible blocks prevent a target. Avoid
   repeated events that make no progress.

Between events, these policies must not rewrite earlier history; they append new
messages only. That supports prefix stability but doesn't prevent invalidation
from tools, instructions, images, TTL, routing, or other causes.

### Reasoning

A reasoning step is one eligible response containing one or more `ThinkingPart`
blocks. Test multi-block responses. The 10–20 window continues between turns
without restoring blocks discarded by earlier maintenance.

Trigger at 20 eligible steps or 10% measurable window use. Remove oldest blocks
until both the count is at most 10 and measurable window use is at most 5%.
Specify inclusive boundaries in tests. Use one window rather than keep-all or
keep-none modes, and preserve signature compatibility: don't send a provider
blocks it cannot accept.

For encrypted reasoning or an unknown window, retain counts and label size as
unknown or partial. Investigate adapter reporting first; don't guarantee
percentages without data. Global budgeting and emergency behavior still apply.

### Tool LOD

These absolute ages from the current step were calibration hypotheses, not
validated defaults:

| Representation | Call age in steps | Generic size in characters |
| -------------- | ----------------: | -------------------------: |
| Full           |              0–24 |                  Unchanged |
| LOD 1          |             25–49 |                      2,000 |
| LOD 2          |             50–74 |                        500 |
| LOD 3          |        75 or more |                        100 |

Age and maintenance events use independent schedules. Generic sizes apply only
to tools without a dedicated policy; each built-in declares its own.

A resumed call can move directly to its eligible level. Levels are monotonic
within a branch and derived from original content, not another summary. Preserve
call/result linkage, IDs, order, and provider-required structure. Don't insert
messages inside tool exchanges or break parallel calls, retries, or multimodal
results.

Register deterministic local input and output policies for every built-in tool.
Prioritize measured boundaries:

- `run`: preserve recognizable command, exit status, timeout, and errors; label
  truncated output so partial data doesn't appear complete.
- `read_file`: preserve path and range with a representation that supports
  reread, without inventing conclusions.
- Search: preserve query, scope, paths, and lines before lengthy snippets.
- Skills: apply the separate resource policy; script output doesn't become
  instructions.

External or unregistered tools use 2,000/500/100-character targets. Preserve
name, ID, and linkage; arguments must remain valid for the adapter. If
arguments, binary data, or structured output cannot be safely transformed,
retain them and record why. Don't hide errors or essential metadata to satisfy a
size target.

## Skill instructions and resources

`load_skill` keeps `SKILL.md` in that skill's dynamic block. A successful text
resource read records execution, skill, path, and the actual requested byte
limit. Every prompt reconstruction rereads it under that skill; repeated reads
update or widen the record without duplication. Test partial reads and repeated
reads with different limits.

The agreement accepted that a later file change can change the prompt and cause
a miss. Missing or unreadable files need an explicit notice; don't pretend an
old version remains available. Resource text must retain its origin and mustn't
be promoted to higher instruction authority.

Don't indiscriminately inject binaries as base64. Preserve identity and handle
them according to format and model capabilities. Don't claim binary content
remains available in instructions when it doesn't.

Only reduce historical `load_skill` or `read_skill_resource` output to a short
acknowledgement when reconstruction in the correct execution is guaranteed. Keep
name, path, success, and reconstruction facts. Failed calls don't activate
skills or resources. Compaction and rollback mustn't lose or resurrect
resources; cover resources whose `SKILL.md` is no longer installed.
`run_skill_script` output remains normal history.

## Analytics and debug

Extend the TUI and `/debug --text` without showing prompt content:

- Latest and previous event: step, reason, version, and
  operational/budget/emergency kind.
- Bands: current value, target, ceiling, component status, and whether a value
  is measured, estimated, partial, or unknown.
- Reasoning: retained/discarded steps, known budget, and unmeasurable
  signatures.
- Tools: exchanges per LOD, separate argument/result reductions, exceptions, and
  reductions not applied.
- Skills: estimated instruction/resource cost and partial, missing, or binary
  resource state without content. Preserve warm dynamic-block highlighting
  together with accessible text labels.
- Cache and cost: separate reads, writes, and normal input; reported versus
  estimated cost and incomplete coverage. A write isn't a hit, and missing
  reporting isn't proof of no cache use.

Distinguish the previous observed request from the next projection. Opening
debug repeatedly must not change the session.

## Calibration and experiment

Build a reproducible read-only simulator over a consistent SQLite copy or
snapshot. Don't output conversation content or read secrets. Group by execution
and respect turns, ordering, parallel calls, protected tools, provider, and
rollback. Compare the existing system, coordinated events, and alternatives.

Estimate accumulated context across every request, not just event before/after
values. Separate instructions, schemas, messages, visible reasoning,
unknown-size reasoning, arguments, and results. Report event counts, first
prefix changes, and limitations. Include stable synthetic reasoning and tool
outliers without inventing billed tokens from encrypted signatures.

Label pricing/cache scenarios, including writes, partial hits, TTL, and routing
where relevant. Prompt size alone doesn't establish billing. Don't publish real
savings percentages without observed provider requests and costs.

Validate representative short and long tasks for success, quality, rereads,
errors, latency, tokens, and cost per task. Neither lower hit rate nor greater
compression alone makes a configuration better. Choose defaults after the
comparison and document samples and biases.

## Implementation plan and acceptance criteria

The original plan used these milestones:

1. **Contracts and baseline.** Define step, IDs, bands, versioned policy, event,
   and simulator; capture existing behavior and preserve prior changes.
   Acceptance: deterministic replay and reproducible comparison without database
   writes.
2. **Execution state and recovery.** Integrate persistence, resume, rollback,
   branches, and subagent isolation. Acceptance: identical views after restart,
   no future events after rollback, and read-only debug.
3. **Coordinator and reasoning.** Implement 10–20-step and 5–10% dual triggers,
   cross-turn continuity, compatible signatures, and emergency behavior.
   Acceptance: batched pruning, idempotency, explicit unknowns, and no loop.
4. **Tool LOD.** Implement registry, fallback, and built-in argument/result
   policies. Acceptance: monotonic results with provider invariants and original
   transcript intact.
5. **Skills.** Complete protected dynamic reads and reduce duplication only when
   safe. Acceptance: project/global resources, failure/success, binaries,
   limits, disk changes, rollback, and compaction are tested.
6. **Debug.** Integrate tables and operational state. Acceptance: actionable
   information, preserved dynamic colors, and no exposed private content.
7. **Calibration.** Run simulations and real tasks when authorized resources
   exist. Acceptance: reproducible results separating observations, estimates,
   and assumptions; adjust or justify defaults.
8. **Final verification.** Run focused tests and the feasible full suite, lint,
   format, configured type checks, diff review, and real adapter serialization
   with simulated transports. Record pre-existing failures, missing tools, and
   unexecuted checks.

## Minimum test matrix

The original matrix covers these distinct risks:

- Bands: below, exactly at, and above each ceiling; independent count/size
  triggers; both targets satisfied where possible.
- Hysteresis: no old-content changes between events, multiple actions per event,
  large jumps, no progress, hard limits, and unknown windows.
- Reasoning: cross-turn continuity, absent reasoning, multiple blocks per step,
  encrypted signatures with unknown size, provider changes, zero windows, and
  windows larger than a turn.
- Tools: parallel calls, errors, timeouts, JSON arguments, MCP, binaries,
  short/long results, steps without tools, and call ordering.
- Skills: `SKILL.md`, text, binary, partial reads, success/failure, deleted or
  changed resources, repeated reads, resume, and per-agent rollback.
- Persistence: restart, simultaneous agents, rollback, alternative branches,
  transactional failures, old sessions, and policy-version changes.
- Debug: no writes or content display, readable incomplete/estimated metrics,
  dynamic highlighting, and narrow-terminal presentation.

## External sources consulted

These sources informed the original design; exact behavior depends on adapter
and provider versions. Check current documentation before implementing changes:

- [Pi system-prompt construction](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/src/core/system-prompt.ts).
- [Pi skills](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md).
- [Pi compaction](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/compaction.md).
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching).
- [Anthropic prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching).

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
