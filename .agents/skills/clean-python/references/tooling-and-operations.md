# Python tooling and operations

Use this reference when creating or reviewing a Python project's development
toolchain, local commands, containers, or environment files.

## Project metadata

Keep the supported Python version, runtime dependencies, development groups,
and tool configuration in `pyproject.toml`. Use `uv` as the project interface:

```text
uv init
uv add <runtime-dependency>
uv add --dev basedpyright pre-commit pytest pytest-cov ruff
uv sync
uv run <command>
```

Do not mix a second dependency workflow into the project unless there is a
documented integration requirement. Do not commit a generated virtual
environment.

## Ruff and basedpyright

Use Ruff for both formatting and linting. Enable the `I` rules for isort-style
import sorting and the `F` rules for unused-import detection. `ruff format`
does not sort imports, so run the linter fixes before the formatter. Run both
commands explicitly in local development and CI; pre-commit is an additional
safety net, not the only quality gate.

```text
uv run ruff check --fix .
uv run ruff format .
uv run ruff check .
uv run ruff format --check .
uv run basedpyright
```

Choose a Ruff rule set that matches the project and document deliberate
exceptions in `pyproject.toml`. A typical Python project includes `I` and `F`
in its selected rules, for example:

```toml
[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "ASYNC"]
```

`F401` is covered by the `F` family. `ruff check --fix` can remove unused
imports and sort imports when these rules are enabled. Review automated fixes
for intentional public exports, especially in a library `__init__.py`; expose
those names explicitly with `__all__` or a deliberate alias, or configure a
narrow per-file exception.

Configure basedpyright with an explicit Python version, source roots, and an
appropriate strictness level. Increase strictness for new code rather than
hiding errors with broad ignores.

## Pre-commit

Pin the `ruff-pre-commit` revision and run both Ruff hooks. The exact revision
must be chosen from a current release when the project is created.

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v<current-release>
    hooks:
      - id: ruff-check
      - id: ruff-format
        args: [--check]
```

This pre-commit configuration is non-mutating and can run in local hooks or CI.
Use the explicit formatter and linter fix commands below when you want to
change files. Do not make a CI gate depend on hooks that rewrite the workspace.

Run all hooks before finishing a change:

```text
uv run pre-commit run --all-files
```

## Makefile for applications

Use a Makefile as a small, discoverable interface over project commands. Keep
targets thin and delegate dependency execution to `uv`.

```make
.PHONY: install format lint typecheck test coverage check

install:
	uv sync

format:
	uv run ruff check --fix .
	uv run ruff format .

lint:
	uv run ruff check .

format-check:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run basedpyright

test:
	uv run pytest

coverage:
	uv run pytest --cov=src --cov-report=term-missing

check: format-check typecheck coverage
	uv run pre-commit run --all-files
```

Use the real source package in the coverage target. Add Docker Compose targets
only when the application has a useful local lifecycle to expose. Keep target
names stable and document environment prerequisites.

## Dockerfile and Compose

Applications must make their runtime reproducible. A Dockerfile should use the
project-supported Python minor version, install locked dependencies, separate
build-time and runtime concerns when useful, and run the process as a non-root
user when the deployment allows it.

Use Docker Compose for local multi-service development and integration tests.
Keep the Compose file focused on service wiring, health checks, ports, volumes,
and development dependencies. Do not place credentials directly in the file.
Use `.env.example` to document the required inputs.

Libraries do not need Docker artifacts unless they have a concrete integration,
documentation, or release workflow that benefits from them.

## Environment files

Commit `.env.example` with safe, realistic placeholders. Include every variable
the application reads, even optional values. Explain defaults and accepted
formats in comments or adjacent configuration documentation.

Ignore the real `.env` file and keep secrets out of source control. Load local
values at the application entrypoint with `python-dotenv` while preserving
variables already provided by the process environment:

```python
from dotenv import load_dotenv

load_dotenv(override=False)
```

Production deployments must provide environment values through the runtime or a
secrets manager. A library must not call `load_dotenv()` during import.
