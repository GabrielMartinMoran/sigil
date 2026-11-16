"""The UnconfiguredModel value of the providers layer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters
from pydantic_ai.settings import ModelSettings

from src.core.providers.factory.no_provider_error import NoProviderError

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
