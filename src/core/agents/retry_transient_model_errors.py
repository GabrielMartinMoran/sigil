"""The RetryTransientModelErrors value of the agents layer."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING

import anyio
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import (
    RetryModelRequest,
)
from pydantic_ai.tools import AgentDepsT, RunContext

from src.core.agents.describe_error import describe_error
from src.core.agents.is_transient import is_transient
from src.core.agents.retry_notice import RetryNotice

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models import ModelRequestContext


DEFAULT_MAX_ATTEMPTS = 5


BASE_DELAY = 0.5


MAX_DELAY = 8.0


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
