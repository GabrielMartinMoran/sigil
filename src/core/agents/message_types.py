"""Application message types, independent of model roles and message content."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext

from src.core.agents.mark import mark
from src.core.agents.origin import origin
from src.core.agents.type_of import KEY, type_of


@dataclass
class MessageTypes(AbstractCapability[Any]):
    """Restore per-part types after the library merges historical envelopes.

    Each instance belongs to one run. Part timestamps survive envelope merging;
    request metadata does not. No text is inspected or changed.
    """

    initial: dict[str, Any] = field(default_factory=origin)
    _saved: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _historical: set[str] = field(default_factory=set, init=False)
    _marked: bool = field(default=False, init=False)

    async def before_run(self, ctx: RunContext[Any]) -> None:
        for message in ctx.messages:
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    key = part.timestamp.isoformat()
                    self._historical.add(key)
                    if key in (getattr(message, 'metadata', None) or {}).get(KEY, {}):
                        self._saved[key] = type_of(message, part)

    def _restore(self, ctx: RunContext[Any]) -> None:
        for message in ctx.messages:
            if not isinstance(message, ModelRequest):
                continue
            for part in message.parts:
                if not isinstance(part, UserPromptPart):
                    continue
                key = part.timestamp.isoformat()
                existing = (message.metadata or {}).get(KEY, {})
                if key in existing:
                    self._saved[key] = existing[key]
                if key in self._saved:
                    mark(message, part, self._saved[key])
                elif not self._marked and key not in self._historical:
                    mark(message, part, self.initial)
                    self._marked = True

    async def before_model_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        self._restore(ctx)
        return request_context

    async def wrap_node_run(self, ctx: RunContext[Any], *, node: Any, handler: Any) -> Any:
        try:
            return await handler(node)
        finally:
            # A failed or cancelled model request still leaves a durable prompt.
            self._restore(ctx)
