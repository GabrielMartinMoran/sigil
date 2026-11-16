# Run the Python tests by component

Choose the component that owns the behavior you changed. Tests are separated by
responsibility, not by whether they use a fixture, a mock, or an event loop. Run
commands from the repository root through `uv`.

## Choose a suite

Each component can be collected and run independently. A module that previously
mixed engine rules with widgets or HTTP routes now has tests in the relevant
component directories.

| Directory         | Contract                                                                                                                                                   |
| ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tests/core/`     | Engine rules, agents, providers, storage, context, tools, and repository tooling. Collection doesn't import Textual or the TUI/server applications.        |
| `tests/tui/`      | Textual widgets, keyboard/focus routing, rendering, UI preferences, the terminal entry point, and the service client, including client/server integration. |
| `tests/server/`   | HTTP/authentication, event streams, runtime/session lifecycle, queues, ownership, service processes, and web delivery/build contracts.                     |
| `tests/support/`  | Shared builders, deterministic models, transports, and widget hosts. Test modules don't import other test modules.                                         |
| `tests/fixtures/` | Shared static inputs, resolved relative to the common test root.                                                                                           |

Use eight workers for an entire component suite:

```sh
uv run pytest -q -p no:sugar -n 8 tests/core
uv run pytest -q -p no:sugar -n 8 tests/tui
uv run pytest -q -p no:sugar -n 8 tests/server
```

Run independent checks concurrently when they don't share mutable state or
compete for resources. Don't launch several eight-worker suites at once. When
multiple components are affected, use one combined selection instead:

```sh
uv run pytest -q -p no:sugar -n 8 tests/tui tests/server
```

For a narrow regression, select its file or node ID without workers. Worker
startup can cost more than it saves for a small selection:

```sh
uv run pytest -q -p no:sugar tests/tui/test_question_panel.py
uv run pytest -q -p no:sugar tests/server/test_server_debug.py
```

A shared engine or protocol change can affect multiple components. Start with
its unit tests, then run the affected integrations. A TUI-only formatting change
doesn't require server tests unless it also changes the service contract. A
server wire-format change requires both server and client contract tests.

## Run complete verification

The complete Python gate still uses eight workers. Worker count isn't a
performance optimization claim; sequential measurements establish whether tests
actually do less work.

```sh
uv run pytest -q -p no:sugar -n 8
uv run ruff check src tests
uv run ruff format --check src tests
```

The Python web-delivery tests don't replace the web client's own checks. For
changes to the web client, run its separate commands:

```sh
cd web
npm test
npm run typecheck
npm run lint
npm run format:check
npm run build
```

## Keep tests isolated and inexpensive

The common `tests/conftest.py` applies to every selection. It disables dotenv
loading, sandboxes the home and default database per test, removes inherited
tuning settings at collection, and blocks live provider-model requests.
`TestModel` and `FunctionModel` remain available. An SDK serialization test can
explicitly enable model requests inside a bounded context only after installing
a controlled test transport; it must close the client afterward.

Keep these boundaries when adding or changing tests:

- Put shared helpers in `tests/support/`, not in another `test_*.py` module.
  Import symbols from their defining support module rather than adding re-export
  layers.
- Keep the root sandbox active. Use `tmp_path` and close resources a test owns.
- Use `memory_databases` only when durability, reopening, locking, migrations,
  or filesystem behavior isn't the contract. Keep the real database for those
  tests.
- Test pure rules directly and widgets in a minimal real host. Mount `SigilApp`
  when commands, focus, keyboard routing, session wiring, or layout is under
  test.
- Wait for observable completion with bounded deadlines. Keep real input and
  layout barriers when the assertion depends on them.
- Consolidate a test only after identifying where each useful assertion remains.
  Similar command names across HTTP and TUI don't make their contracts
  redundant.

See the [performance investigation](../performance/test-suite.md) for
measurements, preserved coverage, and remaining costs.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
