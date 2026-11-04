# Errors, resources, and observability

Use this reference when implementing failure handling, resource ownership,
cleanup, retries, or operational logging.

## Exception boundaries

Define semantic exceptions near the policy that owns them. Keep transport
details, database driver exceptions, and vendor errors at infrastructure
boundaries.

```python
class UserNotFoundError(Exception):
    pass
```

Preserve the original traceback when no translation is needed:

```python
try:
    return repository.get(user_id)
except UserNotFoundError:
    raise
```

Translate a low-level error when the caller needs a stable abstraction and keep
the original cause:

```python
try:
    return repository.get(user_id)
except DatabaseTimeoutError as error:
    raise UserStoreUnavailableError("User store timed out") from error
```

Do not write `except Exception: pass`. At a deliberate process boundary, catch
broad failures only to log useful context, clean up, translate the result, or
perform controlled shutdown. Re-raise unexpected failures after that work.

## Resources and cancellation

Use context managers for resources with a lifecycle. Keep ownership visible in
the function that opens the resource.

```python
from pathlib import Path


def read_config(path: Path) -> str:
    with path.open(encoding="utf-8") as config_file:
        return config_file.read()
```

Do not hide database sessions, clients, locks, or temporary files in module
globals. Inject factories or context managers when a use case owns a resource.
Treat cancellation as a control signal in async and worker code. Clean up and
re-raise cancellation instead of converting it into a normal business error.

## Retries and timeouts

Retry only operations and exception types that are explicitly transient. Set a
bounded retry count, timeout, and backoff policy. Do not retry validation,
authentication, or deterministic domain failures. Make operations idempotent
before adding retries to a worker or HTTP client.

## Logging

Log at the boundary that has the required context. Include operation name,
correlation ID, resource identifier where safe, outcome, duration, and the
exception cause when relevant. Never log passwords, tokens, API keys, or full
request bodies by default.

JSON logging is a useful optional choice for services that aggregate logs or
need machine-readable fields. Use the project's existing logging strategy
instead of introducing `loguru` into a codebase that already uses the standard
library logging API.
