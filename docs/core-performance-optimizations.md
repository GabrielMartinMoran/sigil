# Core performance optimizations

The first four priorities from the [core performance
investigation](core-performance-investigation.md)
are implemented: shell exit handling, bounded byte reads, projection-local
measurement reuse, and cheaper line ranges, image deduplication, and patch
diagnostics. No Rust extension, subprocess-based file reader, or dependency was
added. Persistence fingerprints and skill caches are unchanged.

## Changes and preserved contracts

The changes remove unnecessary work at the existing boundaries rather than
replacing the tool architecture.

- **Shell:** [`drain()`](../src/engine/shell/process.py) drains registered
  streams,
  then waits for process exit using the remaining command deadline. It no longer
  waits on an empty selector. Output capture remains bounded, discarded output
  is still drained, stdout/stderr remain separate, and a command that closes its
  streams without exiting is still terminated at its deadline. Process-group
  termination, noninteractive stdin, cancellation ownership, and exit-code
  policy
  are unchanged.
- **Byte reads:** [`_read_whole()`](../src/engine/files/tools.py) reads at most
  `max_bytes + 1` for nonnegative byte limits. The extra byte establishes
  truncation from the actual read, not a possibly stale `stat()` size. Decoding
  still receives only the requested prefix, preserving UTF-8 tail handling and
  binary/base64 behavior. Existing negative-slice semantics are retained with a
  full read for negative limits.
- **Line ranges:** [`count_lines()` and
  `line_boundary()`](../src/engine/files/text.py)
  count LF-delimited editor lines without allocating one string per line, then
  locate boundaries from the nearer end of the text. Reads still decode the
  whole file to validate UTF-8 and compute exact `total_lines`. CRLF, a missing
  trailing newline, range normalization, and errors remain unchanged. This is
  not constant-time reading of arbitrary ranges.
- **Context:**
  [`ProjectionMeasurements`](../src/engine/context/compaction/measurements.py)
  belongs to one synchronous
  [`AppendOnlyStrategy.view()`](../src/engine/context/strategy/append_only.py).
  [`prepare()`, `finish()`, and
  diagnostics](../src/engine/context/compaction/projection.py)
  share it explicitly. Keys include the ordered message identities and budget;
  changed projections are measured separately. Strong references prevent object
  id reuse. There is no global cache, session cache, or mutable state on the
  strategy, and nothing is persisted. Subsequent views, model changes, and
  in-place history edits start with fresh measurements.
- **Images:** [`source_identity()`](../src/engine/attachments/tools.py)
  identifies
  a session id, URL, or resolved path before loading pixels. An image already
  attached in the current round returns the existing short response without
  reading, downloading, decoding, or recompressing it. Filesystem paths are
  still
  resolved and authorized before the early return, including symlinks. A new
  round can load the image again; new images keep their existing codec behavior.
- **Patches:** [`apply_string_patch()`](../src/engine/files/text.py) counts
  matches
  without storing every offset. Ambiguous diagnostics include the exact total,
  the first ten line locations, and the number of omitted locations. Newlines
  are counted in disjoint spans for that preview. Overlapping match counts are
  preserved, including the existing distinction between an overlapping count and
  Python's nonoverlapping replacement behavior. `replace_all` uses native
  `str.count()` only when the needle cannot overlap itself: its first character
  never occurs again within the needle. Otherwise it counts overlapping matches.

The intentional user-visible change is the bounded ambiguous-patch error. It
still reports the exact count but no longer lists tens of thousands of lines.
The image guard now treats an already-attached source as satisfied without
requiring that source to remain readable or reachable later in the same round.
Filesystem authorization still applies.

## Paired benchmark results

Two final baseline/optimized pairs ran consecutively, not concurrently with
tests
or other checks. Each campaign used three rounds, the same per-case repetition
counts, Python 3.14.6, warm page cache, GC enabled, no coverage, and one
benchmark
process. Values below are medians of per-round mean milliseconds, not individual
latency percentiles.

| Workload | Pair 1 baseline → optimized | Pair 2 baseline → optimized | Paired improvement |
| --- | ---: | ---: | ---: |
| `read_file`, 4 KiB from 32 MiB | 18.654 → 0.331 ms | 18.393 → 0.237 ms | 56–78× |
| `read_file`, lines 1–40 from 32 MiB | 698.855 → 64.280 ms | 702.425 → 59.774 ms | 10.9–11.8× |
| `run`, emit 1 MiB with bounded capture | 152.529 → 4.894 ms | 192.471 → 5.452 ms | 31–35× |
| Context projection, 201 messages | 38.679 → 14.458 ms | 41.664 → 12.099 ms | 2.7–3.4× |
| Context projection, 1,001 messages | 170.310 → 63.752 ms | 217.117 → 74.167 ms | 2.7–2.9× |
| Instant-model read roundtrip with context, 1,001 messages | 318.496 → 191.350 ms | 338.919 → 182.539 ms | 1.7–1.9× |
| Already-attached 1920×1080 image | 538.325 → 0.295 ms | 501.285 → 0.240 ms | Removes pixel processing |
| Reject ambiguous patch, 50,000 matches | 1159.273 → 6.952 ms | 936.108 → 7.765 ms | 121–167× |
| Replace all, 50,000 one-character matches | 4.522 → 0.118 ms | 4.919 → 0.162 ms | 30–38× |

The source-level regression test and profiles confirm that an unchanged measured
projection computes its input fingerprint once rather than four times. The
optimized profile has one `estimate_input()` call and three aggregate token
estimates instead of four and eleven, respectively. A newly rewritten projection
can legitimately require another measurement.

For an example of within-campaign variation, the optimized 1,001-message
projection ranged from 61.064–64.213 ms across round means in pair 1, and from
70.664–76.467 ms in pair 2. The corresponding baseline ranges were
166.094–172.368 ms and 215.508–219.758 ms. Use the raw samples below for other
cases rather than treating the table as a latency guarantee.

### Results that did not uniformly improve

Small operations and unchanged codec work do not justify a universal speedup
claim. The raw data includes these counterexamples:

- A single `printf` increased from 1.38–1.41 ms to 1.70–1.75 ms in the final
  campaigns. The shell change targets the empty-selector tail, not the best-case
  process startup time. Timed `Popen.wait()` can also add a short polling delay.
- Eight parallel `printf` commands improved only 1.05× in pair 1 and 2.75× in
  pair
  2, consistent with the old empty-selector race not occurring every time.
- Small reads varied in either direction: 0.246 → 0.303 ms and
  0.321 → 0.275 ms.
- New image loads were not accelerated: 535–562 ms baseline versus 598–631 ms
  optimized in the whole-harness campaigns. To investigate that apparent
  regression, a follow-up alternated baseline and optimized wrappers in one
  process, against the same fixture, with one warmup each and six timed calls
  each. Medians were 411.09 and 410.62 ms, respectively; samples overlap. This
  smaller controlled check did not reproduce a consistent regression, but does
  not identify the cause of the earlier variation. The codec is unchanged; this
  optimization only targets sources already attached. See the
  [isolated new-image samples](benchmarks/core-performance-new-image-isolated.json).
- The 21-message instant-model roundtrip with context was slower in one pair
  and faster in the other. The benefit is clear for the larger history loads,
  not every tiny request.

Total benchmark process wall time was 61.57 → 28.53 seconds for pair 1 and
62.97 → 29.95 seconds for pair 2. Those totals include fixture setup, warmups,
profiles, prototypes, unrelated tools, and local MCP startup. They are not
application latency measurements or a Python test-suite performance claim.

### Raw evidence and reproduction

The final JSON files contain every round mean, repetition count, environment,
profile, label, and hashes of the seven affected runtime source files. The base
revision is `046badd77e7884ca9f2d04d64e255a5da9900f6d`; optimized files are an
uncommitted working-tree snapshot, not that revision's source. Source hashes
identify the distinction.

- [Final baseline, pair 1](benchmarks/core-performance-baseline-final1.json)
- [Final optimized, pair 1](benchmarks/core-performance-optimized-final1.json)
- [Final baseline, pair 2](benchmarks/core-performance-baseline-final2.json)
- [Final optimized, pair 2](benchmarks/core-performance-optimized-final2.json)

Run the current implementation from the repository root:

```sh
uv run python -m scripts.benchmark_core --rounds 3 \
  --label optimized --output /tmp/sigil-core-optimized.json
```

The baseline was materialized with `git archive HEAD src` in an OS temporary
directory, without checking out or reverting this working tree. Both versions
used the same installed environment and benchmark workload. The copied baseline
benchmark imports its historical memoization prototype from `projection` rather
than the new `measurements` module; this only adapts the prototype to its
original
patch location. It does not change the ordinary tool or context workloads used
in the comparison. `GIT_DIR` identified the original repository for revision
metadata. The final harness's additional source-hash and label metadata is
outside timed regions.

Earlier `core-performance-{baseline,optimized}-pair{1,2}.json` files are
retained
as **intermediate experiments**, not final-tree evidence. They exposed an
initial
regression from collecting preview positions during `replace_all`. A safe native
count fast path fixed it; the final pairs above were measured afterward. The
original investigation's `run1`, `run2`, `web`, and `tree` files remain
historical
results, not measurements of the optimized implementation.

The benchmark's old `candidate/*` rows remain experiments. Do not use those rows
as implementation timings; the ordinary tool and `context/view_*` rows now call
the optimized runtime directly. `candidate/memoized_*` is redundant with the
runtime's local reuse and remains only for historical continuity.

## Verification

Regression coverage targets the changed boundaries without adding another full
application workflow for each rule. The new tests first failed on the previous
implementation for empty-selector waiting, bounded reads, per-view fingerprint
reuse, early image deduplication, and bounded patch diagnostics.

Checks run on the implementation include:

- `uv run pytest -q -p no:sugar tests/test_shell.py`: 30 passed.
- The focused shell/files/images/patch/context/prototype selection with
  `--durations=10`: 343 passed before the final `replace_all` adjustment. The
  affected selection was rerun after that adjustment: 121 passed.
- `uv run pytest -q -p no:sugar -n 8`: first run had one UI pagination failure,
  with 2,858 passed and one skipped. The unchanged failing test passed in
  isolation. Subsequent complete reruns passed with **2,859 passed, one skipped**,
  and one existing `RetryPromptPart` deprecation warning. No UI test, scheduler, wait
  ceiling, or skip was changed to obtain that result. The initial intermittent
  failure is recorded; its root cause was not established by this task.
- Focused coverage across the five affected boundary/helper modules: 160 passed,
  91% aggregate statement coverage; the new measurements module reached 100%.
  Missing lines are predominantly existing error, permission, presentation, and
  exceptional I/O branches, not evidence of full application coverage.
- `uv run ruff check src tests scripts/benchmark_core.py` and
  `uv run ruff format --check src tests scripts/benchmark_core.py`: passed.
- The experimental Clean Python Rust validator returned exit 2. It cannot parse
  existing Python 3.14 exception syntax and reports structural conventions
  across
  existing modules, benchmark helpers, and the new `ProjectionMeasurements`
  filename. This optional validator is not clean and does not replace the
  configured project checks. No static type-checking gate is configured or
  claimed as run.

Tests also compare cached and uncached projections under pressure, instruction
changes, and unknown windows; exercise history mutation and concurrent views;
check line boundaries against the legacy splitter; preserve UTF-8, CRLF, and
negative byte-limit behavior; and exhaust small overlapping-needle cases.

## Next steps

Keep persistence fingerprints, skill caches, and codec changes as separate work.
The remaining full-history fingerprint in each projection still costs time, but
eliminating it or caching across requests requires stronger invalidation rules
than this request-local optimization. Measure real UI and provider workloads
before interpreting synthetic roundtrip improvements as end-to-end gains.
