# Core tools and agent-loop performance investigation

Sigil has measurable optimization opportunities before a Rust migration. The
largest local costs in this investigation come from unnecessary work: reading
entire files, splitting millions of lines, polling an empty selector, repeated
history serialization, and reprocessing images that are already attached.

This records the diagnostic baseline before runtime changes. The first four
priorities are now implemented; see the
[implementation and paired results](core-performance-optimizations.md). The
original measurements below describe the pre-optimization checkout and machine,
not production latency SLOs.

## Findings and priority

Start with the shell drain and bounded reads: both have small implementation
boundaries and measured alternatives. Optimize the context pipeline next because
its cost repeats on every model request and grows with history.

| Priority | Component | Evidence | Recommended action |
| --- | --- | --- | --- |
| P0 | Shell output draining | Approximately 200 ms of avoidable waiting on affected commands | Wait for process exit after both pipes close; retain deadlines, output caps, process-group cancellation, and ownership |
| P0 | Bounded file reads | A 4 KiB request reads a whole 32 MiB file | Read at most `max_bytes + 1`, then decode and construct the same result |
| P1 | Context preparation | 223–310 ms per 1,001-message projection | Calculate estimates once per projected sequence within a request; then investigate incremental history accounting |
| P1 | Line-range reads | 625–820 ms to return 40 lines from a 32 MiB file | Avoid allocating a Python string for every line; preserve total-line and newline contracts |
| P1 | Duplicate image reads | Approximately 513–544 ms even when already attached | Resolve identity before decoding; return early for a known attached identity |
| P1 | Patch diagnostics | 1.02–1.20 s to reject 50,000 ambiguous matches | Avoid recounting each prefix; bound the diagnostic while reporting the full match count |
| P2 | Session saves | 32–59 ms for an unchanged 1,001-message transcript | Avoid reserializing the complete transcript for every synchronization fingerprint |
| P2 | Skill loading | Approximately 6–7 ms to render ten loaded skills per request | Separate body reads from resource inventories; use invalidatable caches |
| P3 | Tool wrappers | Approximately 0.13 ms above a small file read in a paired comparison | Cache signatures and reuse bound arguments; optimize output preview construction |

These priorities reflect recurrence, absolute savings, and implementation risk,
not just speedup ratios. No single application-wide speedup is established.

## Reproduce the measurements

Run the harness from the repository root. It creates synthetic fixtures and a
local MCP server in an automatically removed temporary directory.

```sh
uv run python -m scripts.benchmark_core --rounds 5 --output /tmp/core.json
uv run pytest -q -p no:sugar tests/test_core_benchmarks.py
```

Allow several minutes on a loaded machine. Do not run tests or other benchmarks
concurrently when comparing results. A single external execution deadline of 120
seconds interrupted one intermediate campaign; its incomplete results were
excluded. The completed campaigns took 100.97 s and 65.07 s of process wall
time.

The measurement environment and evidence are:

- CPython 3.14.6, Linux x86-64, 16 logical CPUs.
- Runtime revision `046badd77e7884ca9f2d04d64e255a5da9900f6d`.
- [Campaign 1: five rounds](benchmarks/core-performance-run1.json).
- [Campaign 2: three rounds](benchmarks/core-performance-run2.json).
- [Live web samples](benchmarks/core-performance-web.json).
- [Synthetic tree-control samples](benchmarks/core-performance-tree.json).
- Profiles from `cProfile` are included in each offline campaign's JSON.

Each table entry is the median of **per-call round means**, in milliseconds.
The raw files retain each round mean and its minimum and maximum. These are not
p50/p95/p99 distributions of individual calls. Each case gets an unmeasured
warmup; garbage collection remains enabled, and filesystem page caches are warm.
There is visible cross-campaign variation; use paired comparisons within a
campaign rather than combining timings from different campaigns.

Campaign 2 adds bounded-drain and full-tool bounded-read candidates. The final
harness also includes the tree-control cases, measured separately after the two
campaigns. The core production source was unchanged throughout.

## Base-tool measurements

The local tool cases call the real registered wrappers with a synchronous no-I/O
logger. They do not include provider latency, the permission capability, UI
rendering, or session checkpoints. The loop measurements below separately
include
pydantic-ai dispatch, validation, and its synchronous-tool thread handoff.

| Tool and workload | Campaign 1, ms | Campaign 2, ms |
| --- | ---: | ---: |
| `read_file`: 9 KiB UTF-8 | 0.290 | 0.300 |
| `read_file`: 32 MiB, return at most 4 KiB | 19.605 | 16.576 |
| `read_file`: 32 MiB, lines 1–40 | 819.770 | 625.237 |
| `write_file`: overwrite 7 KiB | 0.263 | 0.212 |
| `patch_file`: string mode, 7 KiB, replace all | 0.430 | 0.342 |
| `patch_file`: line mode, 7 KiB | 0.449 | 0.516 |
| `run`: one `printf` | 1.815 | 1.639 |
| `run`: eight parallel `printf` commands, whole batch | 8.452 | 9.697 |
| `run`: emit 1 MiB of NUL bytes, capture truncated to 64 KiB | 192.507 | 179.097 |
| `list_skills`: rescan 30 synthetic skills | 9.257 | 7.963 |
| `load_skill`: one skill, 21 resources | 1.151 | 0.984 |
| `read_skill_resource`: 1.8 KiB | 0.188 | 0.157 |
| `run_skill_script`: tiny Bash script | 2.682 | 2.865 |
| `update_roadmap`: set ten items, real SQLite with `synchronous=FULL` | 1.508 | 1.785 |
| `ask_user`: immediate synthetic answer, no UI or human wait | 0.053 | 0.059 |
| `view_image`: new 1920×1080 noisy PNG | 581.548 | 594.385 |
| `view_image`: same image already attached in this turn | 512.820 | 543.995 |
| `list_mcp_servers`: status, warm local stdio server | 0.046 | 0.064 |
| `list_mcp_resources`: warm local stdio | 0.919 | 2.103 |
| `read_mcp_resource`: warm local stdio | 0.934 | 2.923 |
| `list_mcp_prompts`: warm local stdio | 0.899 | 2.018 |
| `get_mcp_prompt`: warm local stdio | 1.331 | 2.953 |
| `web_search`: adapter only, transport stubbed | 0.206 | 0.423 |
| `read_url`: adapter only, transport stubbed | 0.301 | 0.685 |

The first `list_mcp_servers(connect=True)` took 906 ms and 1,325 ms
respectively,
including local server startup and imports. Each is a single cold sample, not a
stable cold-start distribution. The normal warm status case does not reconnect.

The shell environment cache is replaced with an empty mapping in this harness.
Login-shell startup and reading user shell exports are intentionally excluded.
Writes use normal OS buffered file operations, not an added `fsync` policy.

### Web and agent-management boundaries

Live network timing is separate from the reproducible offline benchmarks. Three
fresh-process `read_url('https://example.com')` calls succeeded in 89, 126, and
204
ms. One of three searches for `Python asyncio documentation` succeeded in 1,924
ms; the other two raised `DDGSException` after 3,619 and 3,337 ms. Startup
before
the call is excluded. This is evidence of provider/network variability and a
failure mode, not enough evidence to rank alternative web backends.

The harness can reproduce the adapter-only web timings. The six live samples
were collected separately with one tool call per fresh subprocess and an
18-second subprocess deadline; their raw observations are retained in the web
JSON. No successful latency statistic combines failures with successes.

Tree control was measured without starting agents or calling a model:

| Tool | Measured boundary | Median, ms |
| --- | --- | ---: |
| `send_message` | Real wrapper and summoner, synthetic `enqueue` receiver | 0.085 |
| `ack_notifications` | Real wrapper, idempotent ACK, no persistence listener | 0.048 |
| `kill_subagent` | Real wrapper, reset synthetic node state, no live task | 0.073 |
| `list_agents` | Real wrapper, two synthetic nodes | 0.058 |
| `summon` | Not executed: would start an agent run | Not measured |

These do not measure real steering delivery, recursive live cancellation,
subagent construction, model requests, or persistence listeners. `list_agents`
rendering alone grows from 0.25–0.54 ms at 100 synthetic children to 35–55 ms at
1,000: `AgentTree.nodes()` repeatedly scans all nodes through `children()`.
Adjacency indexing is a candidate for unusually large trees, not a priority for
a normal small tree.

External MCP tools, extension tools, and tools supplied by this assistant's host
are not Sigil base-tool implementations and were not benchmarked as such.

## Agent-loop measurements

A deterministic `FunctionModel` produces a `read_file` call, receives its
result,
and produces `done`. The table measures the whole `agent.run()` round trip: two
model requests with no network, one real tool execution, and a final answer.
There is no UI, HTTP/SSE transport, prompt-skill rendering, or checkpoint save
in
these timings. The agents use only the file-tool family, not the complete
catalog.

| Initial history | Context capability | Campaign 1, ms | Campaign 2, ms |
| --- | --- | ---: | ---: |
| 21 messages | Off | 6.159 | 3.949 |
| 21 messages | On | 14.162 | 6.977 |
| 201 messages | Off | 14.080 | 8.927 |
| 201 messages | On | 70.698 | 72.315 |
| 1,001 messages | Off | 41.919 | 46.804 |
| 1,001 messages | On | 449.398 | 324.249 |

Histories contain repeated 2.3 KiB tool-result payloads and ten tool exchanges
per
synthetic run. A recent response carries a compatible provider measurement and
fingerprint. A deliberately generous ten-million-token window isolates ordinary
projection below the pressure threshold. These numbers do not measure summary
model calls or the cost of a production compaction boundary.

Direct context projection costs 223–310 ms at 1,001 messages. In a profiled
projection, `estimate_input()` runs four times, `history_fingerprint()` runs
four
times, and `estimate_messages_tokens()` runs eleven times. Profiling adds
overhead;
its purpose here is call attribution, not a replacement timing result.

The entry path is in
[`ContextViewCapability.before_model_request`](../src/engine/context/capability.py),
with the repeated work in
[`prepare`, `finish`, and
`metrics`](../src/engine/context/compaction/projection.py).
Both the server and terminal checkpoint paths also invoke synchronous
persistence
while processing a turn. In the server, `_stream()` checks for checkpoints on
every event. The existing interval already avoids saving on every token; it does
not make an actual save nonblocking.

### Request-local reuse and persistence

A prototype memoizes estimates only during one synchronous `view()` invocation.
It returns the same `ViewResult` for the measured workload. Correctness tests
also compare a smaller history under normal and pressure budgets.

| Workload | Existing, ms | Candidate, ms | Scope |
| --- | ---: | ---: | --- |
| 1,001-message projection, campaign 1 | 309.769 | 134.204 | 2.31× faster projection |
| 1,001-message projection, campaign 2 | 222.825 | 81.216 | 2.74× faster projection |
| Unchanged 1,001-message session save, campaign 1 | 59.074 | Not implemented | Real SQLite and complete synchronization fingerprint |
| Unchanged 1,001-message session save, campaign 2 | 31.534 | Not implemented | Same boundary |

The prototype uses temporary monkeypatches inside the **single-threaded
benchmark
only**. This is not a production caching design. Production code must pass an
explicit per-request measurement object or use equivalent isolated ownership.
Never globally monkeypatch or share mutable caches between concurrent sessions.

The storage profile attributes approximately 94% of its profiled save time to
`SessionStore._fingerprint()`: the database already writes only a tail, but
[`synchronization.fingerprint`](../src/engine/sessions/synchronization.py) still
serializes all messages. Incremental hashes or explicit revision-based dirty
tracking need tests for stream mutations, rollback, forks, recovery, concurrent
writers, and tree changes. Length and last-object identity alone are not a safe
replacement for the synchronization contract.

## Tool-specific alternatives

Native Python operations already call optimized C implementations. Choosing a
better operation often removes more work than replacing the orchestration
language or starting an external command.

### Read bounded bytes and avoid per-line allocations

[`_read_whole`](../src/engine/files/tools.py) calls `read_bytes()` before
slicing.
A complete-tool prototype using bounded I/O, the same decoder, model
construction,
wrapper, and presentation takes **0.152 ms versus 16.576 ms** in campaign 2,
about
109× faster for this 32 MiB/4 KiB workload. Bare I/O takes approximately 0.010
ms;
that is not the whole-tool speedup.

For range reads, `_read_line_range()` reads and decodes everything, then
`split_lines()` creates millions of strings. A valid-range prototype using
`str.count()` and `str.find()` takes 45–48 ms, versus 625–820 ms for the tool.
This
is a partial-path comparison: it preserves the fixture's content and total line
count, but is not a validated general-purpose replacement for range validation.
It still reads the whole file because `total_lines` is exact. Early stopping
alone would change that contract.

An implementation needs coverage for empty files, invalid ranges, missing final
newlines, CRLF, Unicode separators, invalid UTF-8, symlinks, and concurrent file
changes. For large files, an offset index or a chunked full-file line count may
reduce memory further. A persistent index needs explicit invalidation; `mmap`
is an experiment, not an automatic improvement.

### Remove the shell's empty-selector wait

[`drain`](../src/engine/shell/process.py) continues while streams remain **or**
the
process has not exited. If both pipes close before `poll()` observes exit, the
next `_read_available()` selects on no descriptors with a 0.2-second timeout.

A separate trace of five 1 MiB-output commands observed two empty-selector calls
and **400.7 ms accumulated waiting**. This directly identifies the source of the
roughly 200 ms outliers; the eight KiB read chunk is not the main issue here.

The bounded-drain prototype waits for exit after both pipes close. In campaign
2,
raw command execution falls from **203.331 to 3.017 ms**, about 67× for that
workload. The prototype retains a 64 KiB per-stream capture cap and a deadline;
tests verify output, nonzero exit, truncation, and a process that closes its
pipes
without exiting. Cancellation races, inherited descriptors, descendant
processes,
and owner lifecycle still need the full shell contract review before shipping.

`subprocess.run(..., capture_output=True)` also takes approximately 3.5 ms here,
but buffers unlimited output and is not a safe drop-in substitute. Native async
subprocesses might simplify scaling beyond the thread pool; they are not needed
to fix this wait. A persistent shell would change isolation and cancellation
semantics and is not justified by the small-command timings.

### Short-circuit images before decoding

[`view_image`](../src/engine/attachments/tools.py) computes attached IDs, then
calls `load_source()` before checking membership. That path already reads,
decodes, resizes, and re-encodes the image. Session-image reads also reprocess
already prepared bytes.

Resolve the source identity first, then reject a duplicate before expensive
work.
For paths, keep authorization and symlink checks. For cached new images,
separate
source identity from freshness using validated file metadata or HTTP validators.
Move CPU-heavy image work out of the event-loop coroutine where appropriate;
this improves responsiveness but does not by itself reduce total CPU work.

Pillow already uses native codecs. PNG `optimize=False` was not consistently
faster across campaigns: 599 versus 623 ms in one, 681 versus 559 ms in the
other.
The encoded sizes were nearly identical for this noisy fixture. This does not
justify a blanket encoding-policy change; measure representative screenshots,
photos, transparency, and JPEG fallback first.

### Bound patch diagnostics without changing match semantics silently

[`apply_string_patch`](../src/engine/files/text.py) records every overlapping
match
in Python. On ambiguity it calculates each line number by counting from the
start again. With 50,000 matches this repeatedly scans prefixes and constructs a
very large error message.

A single-pass line-position scan and a bounded list of displayed locations avoid
that failure-path blowup. Unique-match validation can check the first two
matches
before producing diagnostics. On the nonoverlapping single-character fixture,
`str.replace()` plus `str.count()` takes 0.09–0.13 ms, compared with 4.6–6.1 ms
for
the current replace-all helper. This comparison is **not universally
equivalent**:
the current helper counts overlapping matches, while `str.count()` and
`str.replace()` use nonoverlapping matches. Resolve that contract explicitly;
do not report the faster helper as a validated drop-in replacement.

### Cache skills and reduce wrapper work

[`render_loaded_skills`](../src/engine/skills/rendering.py) calls
`registry.load()`
for each loaded skill on every request. That operation reparses YAML and walks
the resource tree, even though the prompt needs the body, not a fresh inventory.
Separate body access from resource listing; cache with invalidation for file
edits, replacement, deletion, workspace rebinding, and symlink changes.

PyYAML's `CSafeLoader` is available in this environment. The frontmatter
microbenchmark is approximately nine times faster than `SafeLoader`, but that
is only YAML parsing, not a ninefold skill-tool improvement. Loader differences
and deployment availability need tests before changing the parser.

[`wrap_tool`](../src/engine/tooling/wrapper.py) binds signatures at start and
end,
then again for guarded calls. It builds event previews even with no logger.
A paired 9 KiB read measures 0.078–0.081 ms raw and 0.203–0.208 ms wrapped.
Cache the signature at registration, reuse effective arguments, and avoid full
`str(result).split()` just to retain 200 characters. Human output is formatted
again in the server result event. Also measure consolidating that work without
breaking live output, replay, or invocation identity.

A synchronous no-op wrapper invoked through a worker costs 0.07–0.23 ms; an
async
logger hub costs 0.21–0.30 ms. `emit_tool_event_sync()` runs async sinks through
`asyncio.run()` when invoked outside a loop, which is relevant to worker-thread
tools. A loop-owned queue is a candidate, with ordering, backpressure, shutdown,
and context propagation kept explicit.

Current `duration_ms` is not complete dispatch latency: it starts after emitting
the start event and is evaluated before preview construction, summary work, and
success-event delivery finish. Measure end-to-end spans before using this field
to prioritize the core.

## What not to optimize first

The evidence does not justify replacing the small file operations, roadmap
state,
permission checks, or warm MCP adapters wholesale. No performance claim about
restricted permission profiles is established here.

Do not start a subprocess to replace a sub-millisecond file read. `rg` is a
reasonable optional backend for a future search tool or shell search workflow,
not for patching or reading small files; it was not installed in this
environment
and was not benchmarked. Do not silently replace Bash with `sh` or disable path
validation for speed.

`orjson`, direct Pydantic JSON serialization, libvips, alternate HTML
extractors,
HTTP-client reuse, and `uvloop` remain experiments, not measured improvements in
this report. Fingerprint changes can invalidate durable baselines, and encoder
changes can alter provider payloads. Benchmark serialization fidelity and
migration behavior before adopting an alternative.

The source is reasonably separated by concern: **7/10, Mostly Clean** for the
reviewed core. The main performance issue is repeated or unbounded work across
otherwise clear module boundaries, not Python syntax or a lack of classes.

## Verification and remaining work

The investigation includes correctness checks for its bounded-read, range,
request-local reuse, and bounded-drain prototypes. They are not a substitute for
production regression coverage or a real-client latency profile.

Executed checks:

```sh
uv run pytest -q -p no:sugar tests/test_core_benchmarks.py tests/test_files.py tests/test_shell.py tests/test_tool_registry.py
# 95 passed in 6.75 s

uv run pytest -q -p no:sugar -n 8
# 2783 passed, 1 skipped, 1 deprecation warning in 116.35 s

uv run ruff check src tests scripts/benchmark_core.py
uv run ruff format --check src tests scripts/benchmark_core.py
# Both passed.
```

No new runtime dependency was added. No production source, user settings,
credentials, or actual sessions were changed. Static type checking was not run;
it is not a configured project gate here.
The experimental Clean Python Rust validator was run and returned exit 2: its
parser rejects existing Python 3.14 exception syntax, and its structural rules
also flag the benchmark's mixed helper classes/functions as well as many
existing modules. That optional check is **not clean** and cannot establish a
valid repository-wide verdict. These findings were not used to justify unrelated
refactoring. No web-client changes were made or claimed as validated.

Next, implement the P0 changes with regression tests, rerun the same workloads,
and add request-level spans covering permissions, tool execution, event
delivery,
prompt rendering, persistence, and time to the next provider request. Capture
individual latency distributions and event-loop responsiveness under concurrent
sessions before deciding which remaining kernel, if any, warrants Rust.
