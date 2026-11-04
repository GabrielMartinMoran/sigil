"""Retries on transient failures of the model provider.

An upstream 502, a proxy 429 or a dropped connection used to bring the whole
turn down: the error reached the UI and the flow stopped. This capability uses
pydantic-ai's ``on_model_request_error`` hook to ask for the response again
while the error is transient and attempts remain.

The retry does not touch the history nor tell the model that there was a failed
attempt: the framework stays on the same step and only repeats the call, so
already executed tools are not run again.

The app runs with ``event_stream_handler``, so pydantic-ai streams
internally and a failure can arrive with ``in_stream=True``. It is retried anyway: if the
stream died before emitting text nothing is seen, and if it died halfway the user
already saw a partial response, but the retry notice in the chat explains why
the turn goes on.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING

import anyio
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import (
    ModelConnectionError,
    ModelHTTPError,
    ModelQuotaExceededError,
    ModelRateLimitError,
    ModelRequestRejectedError,
    ModelServerError,
    ModelUnavailableError,
    RetryModelRequest,
)
from pydantic_ai.tools import AgentDepsT, RunContext

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models import ModelRequestContext

DEFAULT_MAX_ATTEMPTS = 5
"""Total attempts per request, counting the first one."""

BASE_DELAY = 0.5
"""Base delay of the exponential backoff, in seconds."""

MAX_DELAY = 8.0
"""Cap on the wait between attempts, in seconds."""

RETRYABLE_ERRORS = (
    ModelRateLimitError,  # 429
    ModelUnavailableError,  # 503 and its subclass 529 (overloaded)
    ModelServerError,  # 500, 502, 504
    ModelConnectionError,  # dropped connection and transport timeouts
)
"""pydantic-ai categories that are worth another attempt.

Those that do not improve by waiting stay out: exhausted quota or billing,
rejected request (400/401/404/422) and exceeded context window.
"""

RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504, 529})
"""HTTP statuses worth another attempt when the error comes uncategorized."""

NON_RETRYABLE_ERRORS = (
    ModelQuotaExceededError,  # 402 and the 429s of quota/billing
    ModelRequestRejectedError,  # 400/422 and their subclasses (auth, 404, context)
)
"""Categories that never improve by waiting, even if the status looks retryable.

An exhausted-quota 429 shares its status with a rate-limit 429, but the first one
does not resolve itself: without this filter the five attempts would be wasted.
"""


def is_transient(error: BaseException) -> bool:
    """Whether ``error`` is one of those that can be solved by retrying."""
    # The category-based discard goes first: a permanent error with status 429
    # must not slip through the fallback below.
    if isinstance(error, NON_RETRYABLE_ERRORS):
        return False
    if isinstance(error, RETRYABLE_ERRORS):
        return True
    if isinstance(error, ModelHTTPError):
        # Adapters or custom models that do not classify: the status is enough.
        return error.status_code in RETRYABLE_STATUS_CODES
    return False


def describe_error(error: BaseException) -> str:
    """One-line summary of the error, fit to show in the chat."""
    status = getattr(error, 'status_code', None)
    detail = _one_line(getattr(error, 'body', None)) or _one_line(str(error)) or type(error).__name__
    if status is None:
        return f'{type(error).__name__} · {detail}'
    return f'HTTP {status} · {detail}'


def _one_line(value: object, limit: int = 140) -> str:
    if value is None:
        return ''
    text = ' '.join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


@dataclass(frozen=True)
class RetryNotice:
    """Data handed to the notifier when a retry is about to happen."""

    attempt: int
    """Number of the attempt that just failed (starts at 1)."""
    next_attempt: int
    max_attempts: int
    delay: float
    """Seconds to wait before the next attempt."""
    reason: str


@dataclass
class RetryTransientModelErrors(AbstractCapability[AgentDepsT]):
    """Retries the model call on transient provider failures."""

    _: KW_ONLY

    id: str | None = 'retry_transient_model_errors'
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay: float = BASE_DELAY
    max_delay: float = MAX_DELAY
    on_retry: Callable[[RetryNotice], None] | None = None
    """Optional callback (the UI uses it to announce the retry)."""

    async def on_model_request_error(
        self,
        ctx: RunContext[AgentDepsT],
        *,
        request_context: ModelRequestContext,
        error: Exception,
    ) -> ModelResponse:
        attempt = request_context.attempt
        if attempt >= self.max_attempts or not is_transient(error):
            raise error

        delay = self.delay_for(attempt, getattr(error, 'retry_after', None))
        self._notify(
            RetryNotice(
                attempt=attempt,
                next_attempt=attempt + 1,
                max_attempts=self.max_attempts,
                delay=delay,
                reason=describe_error(error),
            )
        )
        if delay > 0:
            await anyio.sleep(delay)
        raise RetryModelRequest()

    def delay_for(self, attempt: int, retry_after: float | None = None) -> float:
        """Wait before the next attempt.

        If the provider asked for a ``Retry-After`` it is honored (capped at
        ``max_delay``); otherwise, exponential backoff with jitter so several
        turns do not retry in sync.
        """
        if retry_after is not None:
            return min(max(retry_after, 0.0), self.max_delay)
        backoff = self.base_delay * 2 ** (attempt - 1)
        return min(backoff * random.uniform(0.5, 1.5), self.max_delay)

    def _notify(self, notice: RetryNotice) -> None:
        if self.on_retry is None:
            return
        try:
            self.on_retry(notice)
        except Exception:  # noqa: BLE001, S110 - notifying must never break the retry
            pass


__all__ = [
    'DEFAULT_MAX_ATTEMPTS',
    'RetryNotice',
    'RetryTransientModelErrors',
    'describe_error',
    'is_transient',
]
