# Repository instructions

These instructions apply throughout this repository. Keep changes focused and
preserve existing user work. Sigil uses Python, pytest, pydantic-ai, and
Textual. Run Python tooling through `uv` from the repository root.

## Test execution

Use **eight pytest-xdist workers for the complete Python suite**. During
implementation, run the smallest relevant selection first; worker startup can
cost more than it saves for a small selection.

```sh
# Focused example: replace the path with the affected tests.
uv run pytest -q -p no:sugar tests/test_question_panel.py

# Complete Python suite: default verification for code changes.
uv run pytest -q -p no:sugar -n 8

# Read-only Python quality checks.
uv run ruff check src tests
uv run ruff format --check src tests
```

Run the complete suite once the code is stable, not after every small edit.
Documentation-only changes need content, link, and diff checks rather than an
unrelated full test run. Run additional checks for any other affected component;
the Python suite does not validate the web client.

## Choose the cheapest sufficient test

Use the testing pyramid to prove behavior with the least setup, I/O, and runtime
that still detects the relevant failure. Favor many fast unit tests, fewer
focused integration tests, and a small set of critical end-to-end tests. Do not
impose arbitrary ratios or duplicate every assertion across layers.

- **Unit:** Test pure transformations, validation, formatting, and business
  rules directly. Inject clocks and external collaborators where appropriate.
- **Integration:** Exercise real boundaries when they are the contract: database
  persistence, serialization, files, processes, APIs, or widget interactions.
- **End-to-end:** Reserve complete application workflows for behavior that lower
  levels cannot adequately establish, including critical cross-layer wiring.

For Textual, test formatting without mounting an app. Use a minimal real widget
host for component behavior. Mount `SigilApp` when app wiring, commands, focus,
keyboard routing, themes, sessions, or layout is part of the assertion. Handler
calls can test state transitions but do not replace keyboard or click coverage
when input routing is the behavior under test.

Use temporary storage and in-memory databases when disk behavior is irrelevant.
Retain real disk tests for durability, reopening, migrations, locking, or other
filesystem-dependent contracts. Use deterministic model responses instead of
real LLM requests. Keep real process and MCP tests where those boundaries
matter; do not replace them with mocks merely to improve timing.

## Optimize tests as part of implementation

Test runtime is a quality constraint, not a separate cleanup project. For each
behavior change, review the cost of the tests you add or touch alongside their
correctness and diagnostic value.

- Start with a focused regression test when behavior can be meaningfully tested.
  Cover relevant success, failure, and boundary cases at the narrowest level.
- Reuse existing coverage instead of adding another expensive app workflow for a
  rule already proven below it. Add higher-level assertions for distinct risks.
- Keep fixtures small. Prepare input directly when typing or live rendering is
  not under test; retain representative real input and streaming coverage.
- Wait for observable completion, events, or workers with bounded deadlines.
  Keep layout barriers when inspecting rendered state. Avoid fixed sleeps and
  repeated idle pauses used as guesses that an operation has finished.
- Isolate temporary paths and mutable state so tests work independently under
  eight workers. Close databases, tasks, apps, clients, and subprocesses within
  their owning test or fixture. Do not depend on test order or user credentials.
- Inspect slow tests after introducing expensive setup or changing a hot test
  group. Remove redundant work and unnecessary I/O within the change boundary;
  do not broaden every feature task into a suite-wide optimization effort.

Preserve meaningful behavioral coverage when reducing cost. Never obtain a
speedup by removing required assertions, skipping failures, weakening contracts,
widening timing ceilings without evidence, or mocking the boundary being tested.
Do not patch Textual's scheduler or private idle machinery to make tests faster.

## Measure and verify

Use evidence to distinguish less work from merely distributing it across CPUs.
For targeted profiling, add `--durations=20` to the relevant pytest command.

When claiming a performance improvement, compare the same test selection and
worker count before and after, with coverage instrumentation either enabled in
both measurements or disabled in both. Run benchmarks consecutively, not in
competition with other checks. Repeat measurements, report variation, and label
single samples. Distinguish pytest's reported duration from total process wall
time, including startup and cleanup.

Use a sequential run when evaluating whether the suite itself does less work:

```sh
uv run pytest -q -p no:sugar --durations=20
```

The default full-suite verification remains `-n 8`; parallel speedup does not
prove a reduction in total work. See the
[test suite performance investigation](docs/test-suite-performance.md) for the
recorded measurements and outstanding sequential goal.

Before finishing, inspect the actual diff and report the commands run, results,
and any failures or blocked checks. Repeat affected verification after fixes;
results from an earlier code tree do not validate later changes.
