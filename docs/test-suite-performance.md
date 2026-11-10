# Test suite performance investigation

The Python test suite remains too slow when it runs sequentially. Its historical
baseline is **147.93 seconds**. Three passing runs of the latest test tree have
an end-to-end median of **102.46 seconds**, with a range of **102.21–105.82
seconds**. The first goal is **73.97 seconds or less**, without reducing
meaningful test coverage. The stretch goal is **36.98 seconds or less**. Neither
goal has been met, and no minimum achievable runtime has been established.
Reaching the first goal is a checkpoint, not a stopping rule: continue while
further improvements preserve the suite's behavioral and integration guarantees.

This document records the investigation, changes, evidence, and remaining work.
The baseline and results include the uncommitted test changes in the worktree;
they are not measurements of a clean checkout of `main`.

## Problem and success criteria

Parallel execution shortens elapsed time by distributing work across processes.
It does not demonstrate that the suite itself performs less work. The primary
metric for this effort is therefore the wall-clock time of the complete Python
suite with **one pytest process and no xdist workers**.

A successful optimization must preserve test quality:

- Keep tests independent, deterministic, and meaningful.
- Retain real integration coverage for behavior that depends on Textual,
  SQLite on disk, operating-system processes, or MCP stdio.
- Replace an integration test with a lower-level test only when the latter
  proves the same relevant behavior, or when the integration boundary remains
  covered elsewhere.
- Do not skip tests, weaken assertions, or remove synchronization solely to
  improve the benchmark.
- Treat parallel execution as a separate developer convenience, not evidence
  that the sequential goal has been met.

## Measurements

The measurements below were taken locally with Python 3.14.6, on a worktree
based on `main` commit `a9b4bc1`. Each row is a single run, not a statistical
summary. The suite and the worktree changed between iterations; machine load and
timing variability can also affect the comparison.

| Run | Wall time | Result |
| --- | ---: | --- |
| Sequential baseline | 147.93 s | 2047 passed, 1 skipped, 1 warning |
| First optimization | 142.33 s | 2047 passed, 1 skipped, 1 warning |
| Second iteration | 130.47 s | 2047 passed, 1 skipped, 1 warning |
| Subsequent iteration | 125.53 s | 1 failed, 2046 passed, 1 skipped |
| Latest passing iteration | 126.95 s | 2047 passed, 1 skipped, 1 warning |

The rows above are historical single-run measurements. The continuation below
records `/usr/bin/time` process wall time separately from pytest's own reported
duration, so startup and final cleanup are not silently omitted.

| Continuation run | Process wall time | Pytest duration | Result |
| --- | ---: | ---: | --- |
| Entry tree | 122.80 s | 118.89 s | 1 failed, 2046 passed, 1 skipped |
| First iteration, run 1 | 115.69 s | 111.90 s | 2051 passed, 1 skipped |
| First iteration, run 2 | 117.22 s | 113.38 s | 2051 passed, 1 skipped |
| Previous checkpoint, run 1 | 106.82 s | 103.00 s | 2057 passed, 1 skipped |
| Previous checkpoint, run 2 | 110.62 s | 106.78 s | 2057 passed, 1 skipped |
| Previous checkpoint, run 3 | 108.84 s | 104.99 s | 2057 passed, 1 skipped |
| Current tree, run 1 | 105.82 s | 102.00 s | 2063 passed, 1 skipped |
| Current tree, run 2 | 102.21 s | 98.46 s | 2063 passed, 1 skipped |
| Current tree, run 3 | 102.46 s | 98.79 s | 2063 passed, 1 skipped |

The final three runs use the same Python test tree, sequentially, without
coverage instrumentation or concurrent validation commands. Their median process
wall time is **102.46 seconds**; the median pytest duration is **98.79
seconds**. Only the latter excludes process startup and final cleanup; the
end-to-end suite has not crossed the 100-second mark. All three report the
existing `RetryPromptPart` deprecation warning.

The process median is 6.38 seconds (5.9%) below the previous 108.84-second
checkpoint. It is 45.47 seconds (30.7%) below the historical 147.93-second
baseline, and 24.49 seconds below the earlier 126.95-second passing result.
These are historical comparisons, not controlled A/B estimates of the complete
suite: tests changed and machine load varied. The failed entry run is not a
successful baseline. Another **28.49 seconds** must be removed from the current
median to meet the first goal. No minimum achievable runtime has been proven.

Run the sequential benchmark from the repository root:

```sh
/usr/bin/time -f 'WALL_SECONDS=%e USER_SECONDS=%U SYS_SECONDS=%S' \
  uv run pytest -q -p no:sugar --durations=0 --durations-min=0
```

`--durations=0` reports individual pytest phases. It helps locate expensive
tests, but rounded per-test durations are not a precise measure of total CPU or
a breakdown of internal work. The web suite is separate and was not measured in
this investigation because its dependencies were not installed.

### Parallel execution: separate wall-clock measurement

The same test tree was also run once with four xdist workers and once with
eight, consecutively rather than concurrently. These are single samples, not
medians. The sequential reference is the preceding three-run median; no new
sequential run was taken for this comparison.

| Execution | Process wall time | Pytest duration | Speedup vs. sequential reference |
| --- | ---: | ---: | ---: |
| One process, previous median | 102.46 s | 98.79 s | 1.00× |
| Four workers | 33.42 s | 32.82 s | 3.07× |
| Eight workers | 22.52 s | 21.91 s | 4.55× |

Both parallel runs passed **2063 tests, with 1 skipped and 1 existing warning**.
Eight workers saved another 10.90 seconds (32.6%) versus four in these samples.
Summed user and system CPU time increased from 82.58 seconds with four workers
to 116.17 seconds with eight; lower wall time does not imply less total work.
These results support parallel execution as a developer convenience, not as
achievement of the sequential 73.97-second target.

The measured commands were:

```sh
/usr/bin/time -f 'WALL_SECONDS=%e USER_SECONDS=%U SYS_SECONDS=%S' \
  uv run pytest -q -p no:sugar -n 4 --durations=0 --durations-min=0
/usr/bin/time -f 'WALL_SECONDS=%e USER_SECONDS=%U SYS_SECONDS=%S' \
  uv run pytest -q -p no:sugar -n 8 --durations=0 --durations-min=0
```

## Findings

The available evidence points to repeated UI setup and event processing as the
largest area to investigate. It does not prove that every test in a UI-related
file spends all of its time in Textual.

### Textual application setup and event processing

In the baseline, 43 files contained `run_test()` calls and the tests in those
files accumulated 114.79 seconds in pytest's call phase. That figure includes
all work in those files, including tests and operations unrelated to Textual. It
is not an isolated measurement of UI cost.

A `cProfile` sample of 58 tests in `test_ui_features.py` and
`test_focus_ownership.py` created 57 `Pilot` instances. The profile showed
repeated work in style application, CSS parsing, rendering, and event handling.
Profiling adds overhead, and cumulative function times can overlap, so those
values cannot be added or extrapolated to the entire suite.

The sample also showed time waiting in the event loop. That does not establish
which waits are unnecessary. `Pilot.pause()` without an argument waits for
activity to settle rather than simply sleeping for a fixed duration.

### SQLite

An earlier synthetic schema-creation experiment showed a large difference
between SQLite in memory and on disk. A more representative A/B comparison of 38
UI tests measured **8.81 and 8.91 seconds in memory**, versus **9.76 and 9.38
seconds on disk**, including pytest startup. The sample suggests a modest
benefit in that module, but does not quantify the benefit across the suite.

The `memory_databases` fixture in `tests/conftest.py` is opt-in and isolates
in-memory databases by test and database path. Tests that verify on-disk history
or persistence must continue to use disk. An in-memory database cannot prove
that data survives closing and reopening a file or is visible to another
process.

### Processes and MCP

Real subprocess startup, shutdown, and timeout tests have unavoidable costs when
they verify operating-system or protocol behavior. Some concurrency and
classification checks can instead use controlled doubles and synchronization,
while representative real-process and MCP tests preserve integration coverage.

### Timing-sensitive test

`test_rendering_a_long_history_is_not_quadratic` previously failed in one
complete run: the large case took 0.443 seconds against a threshold of eight
times the small case, which took 0.054 seconds. It passed alone and in the next
full run. The precise cause of that historical timing outlier was not
reproduced.

The test now checks the operation responsible for the original regression:
restoring 150 or 600 turns must issue exactly **one real Textual mount**, with
all 300 or 1,200 widgets in that call. It still mounts real widgets, settles
layout, verifies the child count, and retains the **2.5-second ceiling** for
restoring the long history. The separate message-order test remains.

A temporary mutation replaced batch mounting with individual mounts. The
150-turn case failed deterministically with `assert 300 == 1`. The mutation was
injected at runtime and is not part of the worktree. This verifies detection of
the known per-message mounting regression without dividing two short, noisy
measurements. It does not prove linear complexity for every possible future
implementation; the absolute ceiling also remains sensitive to extreme load.

### Incomplete and non-hermetic shortcut test

The continuation's entry run failed in
`test_enter_still_sends_a_message_with_the_shortcuts_wired`. That test built an
agent with a real provider model and a dummy key, then waited for eight UI
pauses rather than for the turn to finish. Its assertions could pass after the
input cleared even if the model request failed. In the failing full run,
teardown removed the composer while the turn worker was still using it.

The shortcut-test host now uses `TestModel`; provider metadata still comes from
the configured service. The Enter regression explicitly prohibits real model
requests, waits for workers with a deadline, and requires a completed `ok`
response in the session. The strengthened check failed before switching the
model and passed afterwards. This removes an unintended external dependency and
verifies more than input clearing.

### Experiments not retained

Three experiments did not justify changes to the test environment:

- **Zero-delay condition polling:** `Pilot.pause(0)` retained the screen barrier
  but repeatedly timed out while the MCP loader's thread was still pending.
  The helper retains `Pilot.pause()` so polling gives background threads time
  to run. Worker completion is awaited directly where that is the contract.
- **Freezing the static GC graph:** a 99-test sample took 24.69 seconds without
  freezing and 24.76 seconds with freezing, including process startup and
  cleanup. Peak resident memory was approximately 232,512 KB and 230,996 KB.
  These are single-run samples, not evidence of an improvement. GC behavior
  remains unchanged.
- **Collecting young generations after each test:** calling `gc.collect(1)`
  during teardown of the latest 87-test sample took 28.96 seconds versus
  28.86 seconds without it. No benefit was demonstrated, so explicit per-test
  collection was not added.

GC callback instrumentation in another 99-test sample recorded approximately
2.02 seconds in collection, including 1.60 seconds in generation 2. This shows
that collection contributes to runtime variability, but does not establish the
cause of the earlier history-test failure.

## Changes made

The current worktree contains the following optimizations and supporting tests:

- Provider-screen and picker tests that only need an isolated Textual screen
  use a minimal `App` instead of constructing the complete `SigilApp`. They
  continue to exercise real keyboard and screen interactions.
- Some rendering checks exercise `ToolCallBlock` directly rather than mounting
  the entire application. Integration tests remain for the connection between
  tool calls, results, and the UI.
- Redundant pauses immediately after selected `run_test()` and `Pilot.press()`
  calls were removed. Selected theme-picker waits now stop when the expected
  state is observable, with a bounded retry and explicit assertions.
- The SQLite in-memory fixture is used by selected UI modules and has its own
  isolation tests. Tests with explicit disk-persistence guarantees continue to
  use disk.
- The MCP resource and prompt test reuses one server connection rather than
  starting the same server repeatedly.
- Shell concurrency is tested with a thread barrier instead of commands that
  sleep. MCP shutdown concurrency is tested with an asynchronous barrier.
  Separate tests retain real subprocess and MCP server coverage.
- A subagent wake-up test now waits for the delivered state instead of checking
  the opposite condition.
- `pytest-xdist` was added as an optional way to run tests concurrently. It is
  not the success metric for this investigation.

The continuation adds these changes without modifying production source:

- `ChatApp` in `tests/ui_helpers.py` hosts real transcript lifecycle checks
  without starting unrelated application services. Layout in the complete app
  and preference-command persistence retain their `SigilApp` coverage.
- Five tool-row formatting checks now construct `ToolCallBlock` directly.
  Integration coverage remains for call/result matching, failed outcomes,
  grouping, animation cleanup, resizing, and preference application.
- Agent, provider, workspace, and subagent checks stop when the expected state
  arrives instead of always running 8–30 pauses. The shared `wait_until` helper
  has success, delayed-success, and timeout tests. Awaited node-paint operations
  are asserted directly when they have already completed the relevant work.
- Rendering, debug, budget, and persistence tests populate the composer directly
  when text is only setup data, while still using Enter to submit. Keyboard
  entry remains covered by the dedicated composer and focus tests.
- An isolated tree-modal action test uses a minimal Textual app rather than a
  complete session. MCP no-load/failure tests wait for workers instead of
  sleeping for 0.2 seconds.
- Synchronous activity updates and completed preference commands are asserted
  without extra idle waits. Layout-sensitive waits remain; removing one from
  the pending-tool check demonstrated that width-dependent output still needs
  layout to settle.
- The per-command timeout check uses a short injected timeout policy with real
  subprocesses. Six fast policy-boundary cases were added. Real, unpatched
  one-second timeout and process-group cleanup tests remain.

### Latest iteration: completion and state boundaries

The latest iteration reduces test setup and synchronization work without
changing production source or Textual's scheduler:

- `wait_for_turn` awaits the actual `run_turn` workers and asserts that the turn
  is inactive. It has a deadline and propagates worker failures. Five focused
  tests cover completion, unrelated background workers, a missing active worker,
  an already completed turn, timeout, and failure propagation. Layout assertions
  still use a Pilot barrier, including the Markdown screenshot test.
- Scrolling fixtures use one long real user message instead of mounting 30–60
  separate widgets merely to create overflow. A new real-Textual test checks
  the content, overflow, anchoring, and widget count. Separate tests in
  `test_ui_app.py` retain multi-message mounting and streaming-growth coverage.
- Five question-panel cases exercise state transitions through the mounted
  panel's real selection/submission handlers and public navigation actions.
  Their assertions still cover order, edited answers, submitted custom text,
  unanswered-question recovery, and first-question guidance. Eight cases retain
  keyboard integration, including custom typing, unsent drafts, backward/forward
  navigation, Enter, Escape, and Tab focus handling.
- Provider-screen tests use `Pilot.press(*keys)`, which keeps Textual's per-key
  processing, without an extra idle pause after it returns. Steering tests keep
  representative real typing and Enter submission, but no longer retype setup
  text in tests of ordering, rendering, or post-abort persistence.

The same 87 priority tests took **28.86 seconds before** these changes and
**24.48 and 24.65 seconds afterwards**, including process startup. This is a
local before/after sample, not a statistical estimate of the entire suite or an
attribution of the saving to any one change.

## Verification and limitations

The current Python test tree passes three complete sequential runs: **2063
passed, 1 skipped, 1 warning** each. Six tests were added in the latest
iteration; no existing tests were removed or skipped. The 87-test priority group
also passed twice on the final code before the complete measurements.

Ruff lint and read-only format checks pass for `src` and `tests` (490 files),
and `git diff --check` passes. A 161-test focused run passed with **100%
statement and branch coverage** of `tests/ui_helpers.py` (28 statements and 2
branches). This is not a claim about whole-project coverage. No static
type-checking command is configured in `pyproject.toml`.

The additional experimental Clean Python validator returned exit 2: its parser
rejects existing Python 3.14 syntax in production files, and it reports existing
layout and annotation violations throughout the repository. It is not a passing
quality gate and no unrelated production refactor was attempted to satisfy it.

At the preceding checkpoint, `npm --prefix web test` could not start because
`vite` was not installed. Web dependencies remain absent in this worktree, and
this iteration changes only Python tests and the investigation document. No web
timing is reported; install its locked dependencies before measuring that suite.

Three passing full runs provide more evidence than the previous single run, but
do not establish long-term freedom from intermittent failures. The target is
still unmet, so the overall performance investigation remains open.

## Remaining work

The next iteration needs to reduce the work done by the suite, not merely change
how that work is scheduled.

1. **Preserve the history regression guard.** The deterministic batch-mount
   check and absolute ceiling now replace the short-duration ratio. Retain
   both when changing transcript construction; investigate any new ceiling
   failure rather than widening it automatically.
2. **Audit the remaining expensive UI tests.** Identify tests that assert pure
   validation, formatting, mapping, or state decisions and exercise those at the
   lowest useful boundary. Keep representative end-to-end interactions for
   focus, keyboard handling, navigation, rendering, and worker behavior.
3. **Reduce unnecessary UI setup and waiting.** Profile expensive tests, use a
   minimal host where it preserves the contract, and replace blind polling with
   observable conditions. Verify that shared or reused applications do not leak
   focus, timers, workers, state, or test ordering. Snapshots are candidates for
   visual output only; they do not replace interaction tests.
4. **Extend the SQLite evaluation.** Compare disk and in-memory behavior in
   additional representative modules. Use the opt-in fixture only when file
   persistence, independent connections, locking, and process boundaries are
   not part of the test contract.
5. **Review process-heavy tests.** Move pure orchestration and error mapping to
   controlled tests where appropriate, while retaining real process termination,
   child cleanup, timeout, and MCP protocol coverage.
6. **Measure after each cohesive change.** Run focused tests first, repeat
   sensitive UI tests, then run the complete suite sequentially. Compare
   repeated full runs under similar conditions and report the median and range
   before claiming that a goal has been reached.
7. **Verify the final tree.** Run the full suite, applicable lint and format
   checks, inspect the diff, and document failures or checks that cannot run.
   Measure the web suite separately when its dependencies are available.

### Priorities from the final run

The largest module-level call-phase sums in the third final run identify where
to investigate next. They include all operations in each module, not isolated
measurements of UI setup or CPU time.

| Module | Accumulated call phases |
| --- | ---: |
| `test_focus_ownership.py` | 6.31 s |
| `test_provider_ui.py` | 4.57 s |
| `test_ui_features.py` | 4.48 s |
| `test_steering_and_abort.py` | 4.32 s |
| `test_mcp.py` | 3.94 s |
| `test_workspace_ui.py` | 3.82 s |
| `test_question_panel.py` | 3.70 s |
| `test_picker_keys.py` | 3.57 s |
| `test_question_panel_app.py` | 3.55 s |
| `test_ui_app.py` | 3.51 s |

The remaining gap is larger than any one of these modules. Reaching the first
goal needs additional measured reductions across test boundaries, not just a
smaller polling interval. Keyboard/focus interactions and real process/protocol
checks make up much of this list; retain those guarantees when evaluating
lower-level cases. Shared mutable applications or global patches to Textual's
scheduler are not established safe optimizations.

## Goals and next checkpoint

The next checkpoint is a stable sequential median at or below **73.97 seconds**
with the same meaningful behavioral and integration coverage. The current
checkpoint is **102.46 seconds**, not completion of the performance target.

After reaching the first goal with repeated measurements, continue toward
**36.98 seconds** and beyond that if further justified improvements remain.
Neither threshold authorizes reducing coverage or weakening synchronization. If
further progress requires a larger test redesign or changes a guarantee, record
the measured runtime and explicit trade-offs rather than declaring a minimum
runtime or a completed target without evidence.
