# Typing, imports, and configuration

Use this reference when designing Python module boundaries, public contracts,
configuration loading, or package layouts.

## Complete signatures

Annotate every function and method input and output. This includes constructors,
callbacks, async functions, generators, context managers, and public helpers.

```python
from collections.abc import Iterator


def find_active_users(user_ids: list[int]) -> list[int]:
    return [user_id for user_id in user_ids if user_id > 0]


async def fetch_user(user_id: int) -> User | None:
    ...


def stream_lines(path: Path) -> Iterator[str]:
    ...
```

Use `None` explicitly in the return type when absence is part of the contract.
Prefer `str | None` and built-in generic syntax in Python 3.11+ code. Let local
inference handle obvious local values, but annotate ambiguous collections and
values crossing a boundary.

## ABC and Protocol

Use an abstract base class when the relationship is nominal or when shared
behavior, state, invariants, or lifecycle rules belong to the contract.

```python
from abc import ABC, abstractmethod


class UserRepository(ABC):
    @abstractmethod
    def save(self, user: User) -> None:
        ...
```

Use a Protocol when the consumer needs a structural capability and implementations
do not need to inherit from a common type.

```python
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        ...
```

Do not add an interface just to rename a concrete class. Add it where it protects
a dependency boundary, enables a focused test double, or expresses a stable
capability.

## Direct imports

Applications must make the source of a symbol visible. Import from the module
that defines it instead of depending on a package barrel:

```python
# Clear application import
from app.users.user import User
from app.users.repository import UserRepository
```

Do not use application `__init__.py` files to re-export classes, register
routers, load configuration, or import modules for their side effects. Keep
them empty or omit them when the project intentionally uses implicit namespace
packages. Choose one approach consistently and verify that the project's
packaging and type-checking tools support it.

Libraries may expose a deliberate public API from `__init__.py`:

```python
from .client import Client
from .errors import ClientError

__all__ = ["Client", "ClientError"]
```

Keep library exports stable, explicit, and free of network calls, file access,
configuration loading, or other import-time work.

## PYTHONPATH

Do not set `PYTHONPATH` in a Makefile, Dockerfile, Compose file, test setup, or
shell profile to compensate for a broken project layout. This creates different
import behavior across developers, editors, tests, and deployment environments.

Prefer a clear `src/` layout or another intentional package layout, declare the
project in `pyproject.toml`, install it through `uv`, and run commands with
`uv run`. If an external platform requires `PYTHONPATH`, isolate and document
that platform integration rather than making it the project's normal workflow.

## Environment configuration

Load environment variables at the application boundary, after the process has
started and before typed configuration is built. Do not read environment values
at module import time.

```python
from dotenv import load_dotenv


def load_application_environment() -> None:
    load_dotenv(override=False)
```

Use Pydantic or another typed configuration model when runtime validation is
needed. Keep environment names and parsing in the configuration boundary, then
pass a typed configuration object inward.

Applications must provide a root `.env.example` that lists all variables:

```dotenv
# Required. PostgreSQL connection URL used by the application.
DATABASE_URL=postgresql://user:password@localhost:5432/application

# Optional. Defaults to INFO when omitted.
LOG_LEVEL=INFO

# Required in deployed environments. Use a local-only placeholder here.
API_KEY=replace-with-a-local-development-value
```

The example must never contain live credentials. Keep `.env` ignored and use
runtime environment injection or a secrets manager in deployed environments.
