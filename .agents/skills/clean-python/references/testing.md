# Testing and coverage

Use this reference when designing tests, fixtures, coverage gates, or local and
containerized integration test workflows.

## Test layers

Choose the narrowest test layer that proves the behavior:

- Unit tests cover domain rules and pure transformations without external I/O.
- Application tests use injected fakes or focused mocks for ports and clients.
- Integration tests cover real database, filesystem, broker, or HTTP boundaries.
- End-to-end tests cover a small number of critical user workflows.

Use Docker Compose for integration dependencies when the application profile
requires multiple services. Keep unit tests independent of Compose and a
developer's local environment.

## Test structure

Use Arrange, Act, Assert when it makes setup and behavior clearer. Test names
must describe the behavior and condition. Follow the project's established
convention; when none exists, use a descriptive form such as:

```python
def test_create_user_rejects_duplicate_email() -> None:
    ...
```

Test successful paths, invalid inputs, expected exceptions, dependency errors,
timeouts, retries, cancellation, and resource cleanup when those behaviors are
part of the contract.

Keep fixtures small and local. Use `conftest.py` for fixtures that are genuinely
shared. Avoid fixture graphs that hide the behavior under test.

## pytest-cov

Run coverage as part of the normal quality command:

```text
uv run pytest --cov=src --cov-report=term-missing
uv run pytest --cov=src --cov-report=xml
```

Target the real source package, inspect missing lines, and add a threshold only
when the project has agreed on a meaningful policy. Do not exclude branches or
modules merely to improve a number. Coverage complements behavioral tests; it
does not prove correctness by itself.

## Boundary tests

For FastAPI, test routers through the application's HTTP boundary and test
middleware behavior such as status, duration metadata, correlation IDs, and
redaction. For Click, test command invocation, output, and exit codes. For
Celery, test task behavior without requiring a broker, then add focused
integration coverage for serialization and acknowledgement behavior.
