"""Project request payloads without reducing the original transcript.

Only content-free preparation metadata is persisted on request/response envelopes.
The strategy and debug projection are pure; the request hook owns recording and
budget enforcement. No original part, instructions, or tool payload is rewritten.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import KW_ONLY, dataclass, field, replace
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability

from src.engine.agents.turn_map import request_id_of
from src.engine.context.meter.context_meter import ContextMeter
from src.engine.context.meter.request import RequestSnapshot, snapshot_request
from src.engine.context.overhead import measure_request_parameters
from src.engine.context.strategy.protocol import ContextStrategy
from src.engine.context.strategy.view_result import ViewResult

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.models import ModelRequestContext
    from pydantic_ai.tools import RunContext

    from src.engine.context.budget import ContextBudget


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

    _recovery: ContextVar[dict[str, Any] | None] = field(
        default_factory=lambda: ContextVar('context_recovery', default=None),
        init=False,
        repr=False,
    )

    _last: ViewResult | None = field(default=None, init=False, repr=False)
    _snapshot: RequestSnapshot | None = field(default=None, init=False, repr=False)

    @property
    def last_view(self) -> ViewResult | None:
        return self._last

    @property
    def last_request(self) -> RequestSnapshot | None:
        """Instructions and tools of the last request, for diagnostics."""
        return self._snapshot

    def reset(self) -> None:
        """Forgets the last view and its snapshot.

        Called when another conversation is bound: both describe a request *this*
        session never made, and `/context` would report the strategy of the previous
        one as if it had just been applied.
        """
        self._last = None
        self._snapshot = None

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

    async def before_run(self, ctx: RunContext[Any]) -> None:
        """Capture application records before pydantic-ai merges request envelopes.

        Envelope merging intentionally drops application metadata. A mutable holder
        scoped by ContextVar lets graph tasks consume recovery once without sharing
        frontiers across concurrent runs or nested child agents.
        """
        from src.engine.context.compaction.coordinator import recorded_state
        from src.engine.context.compaction.events import POLICY_VERSION

        state, events = recorded_state(ctx.messages)
        holder: dict[str, Any] = {}
        if ctx.messages and (state.evaluated_steps or events):
            holder['record'] = {
                'version': POLICY_VERSION,
                'state': state.as_row(),
                'events': [e.as_row() for e in events],
                'request_id': request_id_of(ctx),
                'status': 'restored',
                'anchor': _last_part_key(ctx.messages),
            }
        self._recovery.set(holder)

    def _restore_records(self, ctx: RunContext[Any], request_context: ModelRequestContext) -> None:
        from src.engine.context.compaction.coordinator import part_key, recorded_state
        from src.engine.context.compaction.events import RECORD_KEY, CompactionState

        holder = self._recovery.get()
        record = holder.pop('record', None) if holder is not None else None
        if record is None:
            return
        state, events = recorded_state(ctx.messages)
        if state == CompactionState.from_row(record['state']) and [e.as_row() for e in events] == record['events']:
            return
        # Restore onto the owning part's envelope, not the new prompt. A rollback
        # can then discard the new decision without losing an earlier failed one.
        for messages in (ctx.messages, request_context.messages):
            for index, message in enumerate(messages):
                if any(
                    part_key(message, part, offset) == record['anchor'] for offset, part in enumerate(message.parts)
                ):
                    messages[index] = replace(message, metadata={**(message.metadata or {}), RECORD_KEY: record})
                    break

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        # `ModelRequestParameters` is the source of truth of what gets sent:
        # the already-resolved instruction_parts and the schemas the provider serializes.
        self._restore_records(ctx, request_context)
        parameters = request_context.model_request_parameters
        instructions, tools = measure_request_parameters(parameters)
        budget = self.meter.budget().with_measurements(instructions=instructions, tools=tools)
        is_root = getattr(ctx.deps, 'node_id', None) is None
        if is_root:
            self.meter.set_overhead(instructions=instructions, tools=tools)

        result = self._strategy_for_now().view(request_context.messages, budget)
        # Assigning the sequence is request-only; mutating a message in place
        # would affect the persistent history.
        request_context.messages = compatible_model_history(result.messages, request_context.model.model_name)
        if is_root:
            self._last = result
        _require_budget(result, budget)
        if is_root:
            self._snapshot = snapshot_request(parameters, messages=request_context.messages, preview_chars=0)
        self._record_decision(ctx, result, budget)
        if self.on_view is not None:
            # Diagnostics never break the turn.
            with contextlib.suppress(Exception):
                self.on_view(result)
        return request_context

    def _record_decision(self, ctx: RunContext[Any], result: ViewResult, budget: ContextBudget) -> None:
        from src.engine.context.compaction.events import POLICY_VERSION, RECORD_KEY

        planned = result.plan
        if planned is None or not ctx.messages:
            return
        record = {
            'version': POLICY_VERSION,
            'state': planned.state.as_row(),
            'events': [
                replace(
                    event, request_id=request_id_of(ctx), policy=planned.effective_policy, window=budget.window
                ).as_row()
                for event in planned.events[planned.recorded_events :]
            ],
            'effective_policy': dict(planned.effective_policy),
            'window': budget.window,
            'status': 'prepared',
            'request_id': request_id_of(ctx),
            'anchor': _last_part_key(ctx.messages),
        }
        # Original parts stay intact; the transaction persists only decision metadata.
        current = ctx.messages[-1]
        ctx.messages[-1] = replace(current, metadata={**(current.metadata or {}), RECORD_KEY: record})

    async def after_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        from src.engine.context.compaction.events import RECORD_KEY

        record = (ctx.messages[-1].metadata or {}).get(RECORD_KEY) if ctx.messages else None
        return replace(response, metadata={**(response.metadata or {}), RECORD_KEY: record}) if record else response


class ContextBudgetExceeded(ValueError):
    """The protected request cannot fit; never send it to the provider."""


def _require_budget(result: ViewResult, budget: ContextBudget) -> None:
    if budget.fits(result.estimated_tokens) is False:
        raise ContextBudgetExceeded(
            f'Context needs approximately {result.estimated_tokens:,} history tokens, '
            f'but only {budget.available_for_history:,} are available. '
            'Start a new conversation, reduce the current input, or select a larger context window.'
        )


def _last_part_key(messages: Sequence[ModelMessage]) -> str | None:
    from src.engine.context.compaction.coordinator import part_key

    for message in reversed(messages):
        if message.parts:
            return part_key(message, message.parts[-1], len(message.parts) - 1)
    return None


def compatible_model_history(messages: Sequence[ModelMessage], model_name: str | None) -> list[ModelMessage]:
    """Avoid replaying signed reasoning produced by a different model."""
    from pydantic_ai.messages import ModelResponse, ThinkingPart

    result = []
    for message in messages:
        if (
            not isinstance(message, ModelResponse)
            or not model_name
            or not message.model_name
            or message.model_name == model_name
        ):
            result.append(message)
            continue
        parts = [part for part in message.parts if not (isinstance(part, ThinkingPart) and part.signature)]
        if parts:
            result.append(message if len(parts) == len(message.parts) else replace(message, parts=parts))
    return result
