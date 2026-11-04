---
name: clean-python
description: >-
  Use this skill for substantive Python project work where project
  structure, dependencies, tooling, typing, error handling, quality gates,
  imports, configuration, or runtime operations are in scope: create, review,
  refactor, package, configure, containerize, deploy, or change project-wide
  tests. Trigger for uv, Ruff, pytest, pytest-cov, basedpyright, pre-commit,
  pyproject.toml, Dockerfile, Docker Compose, Makefile, imports, PYTHONPATH,
  .env files, environment configuration, or project-level FastAPI, Click,
  Celery, SQLAlchemy, Pydantic, password hashing, secrets, JWT, PyJWT, bcrypt,
  Argon2, or cryptography decisions. Pair it with clean-code for general
  quality and clean-backend-architecture for backend boundaries. Use the
  project's existing conventions for isolated explanations, one-off fixes,
  single-command test runs, domain-only changes, standalone scripts, and
  cloud-only deployment.
license: "MIT"
compatibility: "Python projects; use the project's supported tool versions."
metadata:
  version: "1.1.0"
  author: Gabriel Martín Moran [moran.gabriel.95@gmail.com]
  source: "https://github.com/GabrielMartinMoran/skills"
---

# Clean Python

Build Python code that is explicit, typed, testable, observable, and easy to
operate. Apply the rules below pragmatically: inspect the project before
changing it, preserve an established convention when it is sound, and explain
an intentional deviation.

This skill complements, rather than replaces, the following skills. Use the
applicable companions listed below. If a companion is unavailable, run its
dynamic-use command and follow the complete output.

| Skill | Level | Dynamic use |
| --- | --- | --- |
| `clean-code` | `required` | `npx skills use https://github.com/GabrielMartinMoran/skills --skill clean-code` |
| `clean-backend-architecture` | `recommended` | `npx skills use https://github.com/GabrielMartinMoran/skills --skill clean-backend-architecture` |

Load `clean-code` for general code-quality principles. Load
`clean-backend-architecture` when the project has backend boundaries.

## Operating model

Start every task by identifying the project profile:

- **Library**: reusable package with a public import and distribution API.
- **Application**: deployable service, worker, or standalone process.
- **FastAPI application**: HTTP service with routers, middleware, and boundary
  validation.
- **CLI application**: command-line process with user-facing exit behavior.
- **Worker application**: background process or Celery task consumer.

Then follow this order:

1. Inspect `pyproject.toml`, the source layout, tests, entrypoints, existing
   tool configuration, and the project's supported Python versions.
2. Preserve working project conventions unless they conflict with the rules in
   this skill or create a concrete reliability, clarity, or safety problem.
3. Apply the universal baseline below.
4. Apply only the profile-specific guidance and conditional dependencies that
   the project actually needs.
5. Run the project's quality gates and report any gate that could not run.

Do not add a framework or infrastructure component because it is listed here.
Select a tool when the project requirement justifies it.

## Universal must-haves

Use this baseline for Python projects unless an explicit compatibility
constraint requires a documented exception.

### Toolchain

Use these development tools as the default project toolchain:

| Tool | Purpose |
| --- | --- |
| `uv` | Create environments, resolve dependencies, and run commands |
| Ruff | Format and lint Python code |
| `pytest` | Run unit, integration, and boundary tests |
| `pytest-cov` | Measure meaningful test coverage |
| `basedpyright` | Perform static type checking |
| `pre-commit` with Ruff | Run fast repository checks before commits |

Keep tool configuration in `pyproject.toml` where the tool supports it. Pin
the pre-commit hook revisions in `.pre-commit-config.yaml` and run the hooks
locally. Run them in CI only when the hook configuration is non-mutating; the
explicit Ruff, type-checking, test, and coverage commands are the CI gate. Read
`references/tooling-and-operations.md` before
creating or changing the project toolchain.

Configure Ruff's `I` rules for import sorting and its `F` rules for unused
imports. `ruff format` formats code but does not sort imports; run the Ruff
linter with fixes before formatting. Use `ruff check --fix` only for the
project's configured safe fixes and review changes to intentional exports.

### Types and interfaces

- Annotate every function and method input and return value, including
  `__init__` methods with `-> None`, async functions, generators, callbacks,
  and public class methods.
- Prefer modern syntax such as `str | None`, `list[User]`, and `dict[str, int]`
  for Python 3.11+ projects.
- Use `ABC` and `@abstractmethod` for nominal contracts, shared invariants, or
  behavior that implementations must explicitly provide.
- Use `Protocol` for structural contracts when duck typing and lightweight
  substitution improve decoupling. Do not create an interface without a
  concrete boundary or testing benefit.
- Prefer typed dataclasses, Pydantic models, or domain types over unbounded
  dictionaries at internal boundaries.

### Errors and resources

- Raise specific exceptions for failure conditions instead of returning error
  codes or silently returning malformed values.
- Use bare `raise` inside an exception handler when preserving the same error
  and traceback.
- Use `raise NewError("context") from error` when translating an error or
  adding a meaningful abstraction boundary while preserving its cause.
- Catch the narrowest exception that can be handled. Catch broad exceptions
  only at a deliberate process or transport boundary where the error is logged,
  translated, or causes a controlled shutdown.
- Validate untrusted input at the boundary and preserve domain invariants in
  the domain or application layer.
- Use context managers and explicit ownership for files, database sessions,
  network clients, locks, and other resources.

Read `references/errors-and-resources.md` for examples and boundary rules.

### Security and secrets

Apply this profile when the project authenticates users, issues tokens, stores
API keys, or handles data that must be protected.

- Never store passwords in plaintext or encrypt them for later recovery. Use an
  adaptive password hash, preferring Argon2id through `argon2-cffi` for new
  applications and using `bcrypt` only for compatibility or legacy constraints.
- Let the password hashing library generate and encode a unique salt. Choose a
  work factor by benchmarking the deployment, and rehash successfully verified
  passwords when the configured parameters become outdated.
- Treat a pepper as optional defense in depth. Keep it outside the database in a
  secret manager, and document that rotating it may require password resets.
- Use `secrets` for reset tokens, API key material, and other unpredictable
  values. Hash verify-only tokens in storage and give them expiry and one-time
  use semantics.
- Use `PyJWT` only when JWT is an explicit requirement. Pin allowed algorithms,
  validate required claims, and keep signing keys in the runtime secret or key
  management system.
- Do not place passwords, tokens, keys, full authorization headers, or sensitive
  payloads in logs, exceptions, `.env.example`, or JWT claims.
- Use established cryptographic libraries and key-management services for data
  that must be recovered. Do not implement encryption, signing, key derivation,
  or token validation primitives yourself.

Read `references/security-and-secrets.md` for the password, secret, JWT, and key
management rules.

### Imports and configuration

- Import directly from the module that defines a symbol. Do not make the import
  graph depend on hidden re-exports.
- In applications, keep `__init__.py` empty or omit it when intentionally using
  an implicit namespace package. Do not use it as a barrel, registration point,
  configuration loader, or side-effect hook.
- In libraries, `__init__.py` may expose a deliberate stable public API. Keep
  exports explicit, document the public surface, and avoid import-time work.
- Do not set or override `PYTHONPATH` to make application imports work. Fix the
  project layout, package metadata, and installation workflow instead.
- Applications may use `python-dotenv` at the startup/configuration boundary
  for local `.env` files. Preserve variables already supplied by the process
  environment and never load `.env` as a side effect of importing a library.
- Applications must keep a root `.env.example` in version control with every
  required and optional variable, safe examples, defaults, accepted formats,
  and security notes. Never commit real secrets.

Read `references/typing-imports-and-configuration.md` before changing package
layout or environment loading.

### Testing and coverage

- Test behavior at the narrowest boundary that proves it.
- Cover successful paths, validation failures, expected exceptions, and
  integration failures that can affect the boundary.
- Keep tests isolated, deterministic, and independent of a developer's shell
  environment.
- Use `pytest-cov` to expose untested paths. Set a project-specific coverage
  policy when a threshold is meaningful; do not chase a percentage by weakening
  tests or excluding important code.

Read `references/testing.md` for the test and coverage workflow.

## Application operations baseline

For deployable applications, include the following operational interface:

- A `Makefile` that exposes discoverable targets such as `install`, `format`,
  `lint`, `typecheck`, `test`, `coverage`, `check`, and application lifecycle
  commands.
- A reproducible `Dockerfile` with a project-supported Python version, locked
  dependencies, a non-root runtime where practical, and no development-only
  tools in the runtime image.
- A `compose.yaml` or equivalent Docker Compose configuration for local
  development and integration dependencies. Keep secrets out of the file and
  document required variables in `.env.example`.
- A single documented quality command, such as `make check`, that runs the
  formatter check, linter, type checker, tests, and coverage.

Do not require these artifacts for a library when they do not serve a real
development or distribution workflow. Use `uv run` and the installed project
layout instead of `PYTHONPATH` workarounds.

Read `references/tooling-and-operations.md` for the project-level patterns.

## Conditional dependencies

Add these dependencies only when the corresponding boundary exists:

| Dependency | Use it when |
| --- | --- |
| `pydantic` | Runtime validation or typed external configuration is needed |
| `sqlalchemy` | The application persists data in relational databases |
| `fastapi` | The application exposes an HTTP API with FastAPI |
| `celery` | The application needs distributed background task execution |
| `click` | The application exposes a Click-based CLI |
| `argon2-cffi` | The application stores user passwords and can use Argon2id |
| `bcrypt` | Password compatibility with an existing bcrypt-based system is needed |
| `PyJWT` | The application explicitly issues or validates JSON Web Tokens |
| `cryptography` | The application must encrypt recoverable data using managed keys |

Nice-to-have options include `pytest-sugar` for local test readability,
`loguru` when the project has no established logging strategy, and JSON
logging when structured operational logs are useful. Do not introduce these
options merely because they are available.

## Profile rules

Apply only the profile that matches the project.

### FastAPI

- Group endpoints by capability or bounded context in separate router modules.
- Compose routers explicitly from an application or API composition module.
- Keep routers focused on authentication, boundary parsing, delegation, and
  response translation. Keep business policy outside the router.
- Add HTTP middleware when request observability is required. Log safe
  metadata such as method, route, status, duration, and correlation ID; do not
  log credentials, tokens, or request bodies by default.
- Translate domain and application errors into HTTP responses at the HTTP
  boundary rather than assigning HTTP status codes to domain exceptions.

Read `references/fastapi.md` for the router, middleware, and testing patterns.

### CLI

- Keep the Click command module thin and delegate to an application use case.
- Separate commands by capability and keep parsing, presentation, and process
  exit behavior at the CLI boundary.
- Return stable exit codes and actionable messages without leaking tracebacks
  to normal users.

### Workers and persistence

- Keep Celery task functions thin, idempotent, and explicit about retryable
  errors, timeouts, cancellation, and acknowledgement behavior.
- Keep SQLAlchemy models and sessions at the persistence boundary. Map between
  persistence records and domain types instead of leaking ORM objects inward.
- Inject clocks, repositories, clients, and task gateways so application logic
  remains testable.

Read `references/cli-workers-and-persistence.md` for these profiles.

## Quality gate

Run the project's configured commands after implementation. A typical Python
application uses an equivalent sequence:

```text
uv run ruff check --fix .
uv run ruff format .
uv run basedpyright
uv run pytest --cov=src --cov-report=term-missing
uv run pre-commit run --all-files
```

Use the actual source package in the coverage target and respect project
configuration. If a command is unavailable, install it through `uv` or report
the blocker instead of bypassing the gate.

For non-mutating CI checks, run `uv run ruff check .` followed by
`uv run ruff format --check .`. The import ordering and unused-import rules
must be active in the project's Ruff configuration.

Before finishing, confirm that:

- All changed function and method signatures are typed.
- Imports are direct and do not rely on `PYTHONPATH` or application barrels.
- Error paths preserve useful causes and context.
- Tests cover the changed behavior and failure modes.
- Coverage output was reviewed rather than merely generated.
- Application configuration is documented in `.env.example`.
- Application Docker, Compose, and Makefile commands are reproducible.
- The appropriate companion skills were used for general clean code and
  backend architecture.
