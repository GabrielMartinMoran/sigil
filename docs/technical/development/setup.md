# Set up a development checkout

Sigil uses Python, uv, pytest, pydantic-ai, and Textual. Run Python tooling
through `uv` from the repository root. Node is needed only when changing or
rebuilding the web client.

## Prepare the environment

Follow the
[installation prerequisites](../../usage/getting-started/installation.md), then
prepare the checkout:

```sh
uv sync
./sigil
```

Starting here uses Sigil's repository as the workspace. Use the launcher's
absolute path from a different project when testing workspace discovery.
`./sigil` and `uv run ./main.py` enter the same program, but the launcher
handles its own path and environment when invoked elsewhere.

The launcher may enable this clone's tracked Git hooks. Read the
[hook guide](git-hooks.md) before relying on a commit's automatic fixes.

## Verify a change

Start with the cheapest focused regression, then use the
[testing guide](testing.md) for affected components and full verification. The
standard Python checks are:

```sh
uv run pytest -q -p no:sugar -n 8
uv run ruff check src tests
uv run ruff format --check src tests
```

Use eight workers for a complete suite or an entire component. Don't run several
eight-worker suites at once. For a narrow selection, omit workers. Benchmark
comparable selections consecutively, without competing checks.

Documentation-only changes need content, local-link, and diff checks, not the
unrelated application suite. Never include a live-provider evaluation in a
routine check without authorization.

## Next steps

Use the [web guide](web-client.md) for client work or the
[local extension guide](local-tools.md) to add tools without changing the core.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
