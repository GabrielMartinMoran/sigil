"""Permission-aware dispatch for sandbox-originated tool calls."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import ToolDenied


class NestedDispatcher:
    """Dispatch an explicitly allowed, bounded set of nested calls through the run's manager."""

    def __init__(self, ctx: RunContext[Any], allowed: frozenset[str], *, max_calls: int) -> None:
        if max_calls < 1:
            raise ValueError('max_calls must be positive')
        if ctx.tool_manager is None:
            raise RuntimeError('The current run has no tool manager')
        self._manager = ctx.tool_manager
        self._allowed = allowed
        self._remaining = max_calls
        self._write_lock = asyncio.Lock()

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        """Validate and invoke one allowed tool under the current run's capabilities."""
        if name not in self._allowed:
            raise ValueError(f'Tool {name!r} is not available in code execution')
        if self._remaining <= 0:
            raise ValueError('Nested tool call limit reached')
        self._remaining -= 1
        if name == 'write_file':
            async with self._write_lock:
                result = await self._manager.handle_call(ToolCallPart(name, args), wrap_validation_errors=False)
        else:
            result = await self._manager.handle_call(ToolCallPart(name, args), wrap_validation_errors=False)
        if isinstance(result, ToolDenied):
            raise PermissionError('Nested tool call denied')
        return result
