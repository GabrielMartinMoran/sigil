"""A model that refuses to run because nothing has been configured yet.

The agent needs *a* model at construction time, but the first run of the app has
zero provider accounts. Instead of inventing a fake account or shipping a dummy
key, the router starts with this: it satisfies the ``Model`` contract and fails
the moment a request is attempted, with a message that says exactly what to do.

That keeps the invariant "the agent is always constructible" without opening a
path where a request silently goes somewhere unexpected.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings

NO_PROVIDER_MESSAGE = 'No provider is configured yet. Run /provider to add one.'


class UnconfiguredModel(Model):
    """Stands in for a real model until the user configures one."""

    @property
    def model_name(self) -> str:
        return 'unconfigured'

    @property
    def system(self) -> str:
        return 'sigil'

    @property
    def context_window(self) -> int | None:
        return None

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        raise NoProviderError(NO_PROVIDER_MESSAGE)

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any | None = None,
    ) -> AsyncIterator[Any]:  # pragma: no cover - never reached
        raise NoProviderError(NO_PROVIDER_MESSAGE)
        yield  # make this a generator for type checkers


class NoProviderError(Exception):
    """Raised when a turn is attempted with no configured provider."""
