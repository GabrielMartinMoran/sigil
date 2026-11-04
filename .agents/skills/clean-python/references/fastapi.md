# FastAPI applications

Use this reference when the project exposes HTTP endpoints with FastAPI. Keep
transport concerns at the HTTP boundary and use `clean-backend-architecture`
for deeper domain, application, and infrastructure separation.

## Routers by capability

Group related path operations in separate router modules. A router should have
one coherent capability, common prefix, tags, dependencies, and response
metadata where appropriate.

```python
# api/routers/users.py
from fastapi import APIRouter

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}")
async def get_user(user_id: int) -> UserResponse:
    ...
```

Compose routers explicitly from an application or API composition module:

```python
# api/app.py
from fastapi import FastAPI

from app.api.routers import users


def create_app() -> FastAPI:
    app = FastAPI()
    app.include_router(users.router)
    return app
```

Do not re-export routers or register them from `__init__.py`. Keep the router
focused on authenticating, parsing, delegating, and translating responses.

## Request and response middleware

Use HTTP middleware when every request needs shared observability or policy.
Record safe fields such as method, route, status code, duration, and correlation
ID. Generate or propagate a correlation ID at the boundary and return it when
the API contract requires it.

Do not log authorization headers, cookies, API keys, passwords, or complete
request and response bodies by default. If a debugging use case requires body
logging, make it explicit, redacted, bounded, and disabled in production.

```python
import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

logger = logging.getLogger(__name__)
CallNext = Callable[[Request], Awaitable[Response]]


def add_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def log_request(request: Request, call_next: CallNext) -> Response:
        started_at = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            "http_request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round(duration_ms, 2),
            },
        )
        return response
```

Type the middleware callable according to the project's FastAPI and Starlette
versions when strict checking requires it. Keep logging failures from masking
the original response unless observability is itself a deliberate availability
requirement.

## Boundary validation and errors

Use Pydantic models for untrusted request and response shapes when the project
needs runtime validation. Keep domain invariants in domain objects or use cases
even when a request model validates the same basic shape.

Map application and domain exceptions to HTTP responses in the HTTP boundary.
Do not add HTTP status codes to domain exception classes. Register focused
exception handlers for stable public error contracts.

## Blocking work and tests

Do not run blocking database, filesystem, or CPU-heavy work directly in an
async path operation without an intentional execution strategy. Inject clients
and repositories so application tests do not require a running server.

Test the app through its HTTP boundary for router, validation, middleware, and
error mapping behavior. Keep domain and application tests independent of
FastAPI.
