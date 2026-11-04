# CLI, workers, and persistence

Use this reference when the Python project is a Click CLI, a Celery worker, or
uses SQLAlchemy for persistence.

## Click CLI

Keep Click commands as adapters. They parse arguments, invoke an application
use case, format a user-facing result, and choose a process exit code.

```text
cli/
  main.py          # command group and entrypoint
  users.py         # user-facing commands
application/
  users/create.py  # use case
domain/
  users/           # entities, value objects, ports
```

Do not put business rules, database sessions, or HTTP clients in command
functions. Test command output and exit codes separately from application rules.

## Celery workers

Keep task functions thin and pass a typed command or identifier to an
application use case. Make task behavior explicit about idempotency, retryable
exceptions, timeouts, cancellation, acknowledgement, and dead-letter handling.

Do not retry validation or deterministic domain errors. Add retries only after
the operation has a safe idempotency strategy. Test task behavior without a
broker and add integration tests for serialization and delivery semantics.

## SQLAlchemy

Keep SQLAlchemy models, sessions, queries, migrations, and transaction details
inside infrastructure or persistence modules. Define repository contracts at
the layer that owns the policy needing them. Map rows and ORM models into
domain or application types at the boundary.

Do not pass a live session into domain code. Make transaction ownership visible
in the use case or infrastructure adapter and test rollback and constraint
failures at the persistence boundary.

## Pydantic

Use Pydantic where runtime parsing and validation are needed, especially at HTTP,
CLI, configuration, message, and external API boundaries. Do not use a transport
schema as an excuse to leak framework or serialization concerns into the domain.

Keep configuration models close to the configuration boundary. Convert validated
external data into the smallest stable command or domain type required by the
application.
