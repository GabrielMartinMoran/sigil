"""Capability that applies the context strategy to each request.

It is deliberately **non-destructive**: it only touches `request_context.messages`, which
is a list owned by the request, and leaves `ctx.messages` untouched. That way
`run.all_messages()` keeps returning the full log and the session loses nothing.

pydantic-ai's `ProcessHistory` is no good for this: it does `ctx.messages[:] = ...`
and rewrites the persistent history.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass, field, replace
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability

from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.request import RequestSnapshot, snapshot_request
from src.engine.context.overhead import measure_request_parameters
from src.engine.context.strategy.protocol import ContextStrategy
from src.engine.context.strategy.view_result import ViewResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import ModelRequestContext
    from pydantic_ai.tools import RunContext


@dataclass
class ContextViewCapability(AbstractCapability[Any]):
    """Projects the history onto the model view before each request."""

    _: KW_ONLY

    strategy: ContextStrategy
    meter: ContextMeter
    """Updated with the real cost of instructions and tools of each request."""

    on_view: Callable[[ViewResult], None] | None = None
    """Diagnostics callback. It must not break the request if it fails."""

    provider: Callable[[], str | None] | None = None
    """The provider the next request goes to, read per request.

    The model can be swapped mid-session and the strategy is built once, so this is
    a callable, not a value. Signed reasoning is only replayed to the provider that
    produced it.
    """

    id: str | None = 'context_view'
    """A single view per agent: two different strategies make no sense."""

    _last: ViewResult | None = field(default=None, init=False, repr=False)
    _snapshot: RequestSnapshot | None = field(default=None, init=False, repr=False)

    @property
    def last_view(self) -> ViewResult | None:
        return self._last

    @property
    def last_request(self) -> RequestSnapshot | None:
        """Instructions and tools of the last request, for diagnostics."""
        return self._snapshot

    def project(self, messages: Sequence[ModelMessage]) -> ViewResult:
        """Projects a history with the strategy, without touching the run state.

        This is what the debug view uses: it wants to know what the agent would send
        *now*, not what it sent on the previous request.
        """
        return self._strategy_for_now().view(messages, self.meter.budget())

    def _strategy_for_now(self) -> ContextStrategy:
        """The strategy, pointed at the provider the next request is going to.

        `replace` keeps the strategy a frozen value object: the provider is the only
        thing that changes between requests, and only when the model is swapped.
        """
        if self.provider is None:
            return self.strategy
        return replace(self.strategy, provider=self.provider())

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        # `ModelRequestParameters` is the source of truth of what gets sent:
        # the already-resolved instruction_parts and the schemas the provider serializes.
        parameters = request_context.model_request_parameters
        instructions, tools = measure_request_parameters(parameters)
        self.meter.set_overhead(instructions=instructions, tools=tools)
        self._snapshot = snapshot_request(parameters)

        result = self._strategy_for_now().view(request_context.messages, self.meter.budget())
        # Assigning the sequence is request-only; mutating a message in place
        # would affect the persistent history.
        request_context.messages = result.messages
        self._last = result
        if self.on_view is not None:
            # Diagnostics never break the turn.
            with contextlib.suppress(Exception):
                self.on_view(result)
        return request_context
