"""Application message types, independent of model roles and message content."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import RunContext

MessageType = Literal['user', 'agent_message', 'subagent_report', 'internal_notice']
KEY = 'sigil_message_types'


def origin(message_type: MessageType = 'user', *, reports: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {'type': message_type}
    if reports is not None:
        result['reports'] = reports
    return result


def type_of(message: ModelMessage, part: Any) -> dict[str, Any]:
    """Untyped historical parts are user input, even if they resemble a callback."""
    entries = (getattr(message, 'metadata', None) or {}).get(KEY, {})
    return entries.get(part.timestamp.isoformat(), origin()) if isinstance(part, UserPromptPart) else origin()


def input_views(message: ModelMessage, part: UserPromptPart) -> list[dict[str, Any]]:
    """Shared visibility policy for terminal and web transcript adapters."""
    classification = type_of(message, part)
    kind = classification['type']
    if kind == 'internal_notice':
        return []
    if kind != 'subagent_report':
        return [{'kind': kind, 'content': part.content}]
    items = [{**snapshot, 'kind': kind} for snapshot in classification.get('reports', [])]
    if classification.get('user_text'):
        content = classification['user_text']
        if not isinstance(part.content, str):
            content = [content, *(item for item in part.content if not isinstance(item, str))]
        items.append({'kind': 'user', 'content': content})
    return items


def mark(message: ModelRequest, part: UserPromptPart, value: dict[str, Any]) -> None:
    existing = (message.metadata or {}).get(KEY, {})
    if existing.get(part.timestamp.isoformat()) == value:
        return
    metadata = dict(message.metadata or {})
    metadata[KEY] = {**metadata.get(KEY, {}), part.timestamp.isoformat(): value}
    message.metadata = metadata


def request(text: str, value: dict[str, Any]) -> ModelRequest:
    part = UserPromptPart(text)
    message = ModelRequest(parts=[part])
    mark(message, part, value)
    return message


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
