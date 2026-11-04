"""Concurrency guards for tools called in parallel.

pydantic-ai runs the sibling calls of the same model response as
concurrent tasks. For almost every tool that is fine. For the ones that mutate
a shared resource, two simultaneous calls stomp on each other: each one reads the
original state and the last one to write erases the work of the previous one.

A guard declares, per tool, when a call cannot coexist with the
previous calls of the same response. It receives the invocation context
(including those previous calls, in emission order) and returns the reason
for the conflict, or ``None`` if the call can run in parallel.

The guard is evaluated in invocation order: the first call of a conflicting
group always goes through, and the following ones fail with an actionable message. The
model sees the failure as a tool result and can re-emit the call in a
separate step.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart


@dataclass(frozen=True, slots=True)
class SiblingCall:
    """A call emitted before the current one in the same response."""

    tool_name: str
    args: dict[str, Any]
    tool_call_id: str | None


@dataclass(frozen=True, slots=True)
class ConcurrencyContext:
    """What a guard sees when deciding whether the call can run in parallel."""

    tool_name: str
    """Name of the tool that is about to run."""

    args: dict[str, Any]
    """Effective args of the call, with the defaults applied."""

    tool_call_id: str | None
    """Id of the current call, if the model or the framework assigned one."""

    siblings: tuple[SiblingCall, ...]
    """Calls emitted before this one in the same response, in order."""


ConcurrencyGuard = Callable[[ConcurrencyContext], str | None]
"""Returns the reason for the conflict, or ``None`` if the call can run."""


class ToolConflict(Exception):
    """The call cannot run alongside its siblings of the same response."""


def _as_sibling(part: ToolCallPart) -> SiblingCall:
    return SiblingCall(
        tool_name=part.tool_name,
        args=part.args if isinstance(part.args, dict) else {},
        tool_call_id=part.tool_call_id,
    )


def _current_index(
    parts: Sequence[ToolCallPart],
    tool_call_id: str | None,
    tool_name: str,
    args: dict[str, Any],
) -> int | None:
    """Position of the current call inside the response."""
    if tool_call_id is not None:
        for index, part in enumerate(parts):
            if part.tool_call_id == tool_call_id:
                return index
    # Without an id (or with an id that does not appear) it falls back to the first match by name and
    # args. It is a heuristic, but it only decides which calls are considered
    # earlier: over-guarding makes the guard fail, it never loses data.
    for index, part in enumerate(parts):
        if part.tool_name == tool_name and part.args == args:
            return index
    return None


def earlier_siblings(
    messages: Sequence[ModelMessage],
    *,
    tool_call_id: str | None,
    tool_name: str,
    args: dict[str, Any],
) -> tuple[SiblingCall, ...]:
    """Calls prior to the current one in the response that originated them.

    While a tool runs, the last message of the run is the
    ``ModelResponse`` that requested every call in the batch. If it is not (or if
    the current call does not appear) an empty tuple is returned: with no
    sibling information, the guard has nothing to compare against.
    """
    if not messages:
        return ()
    last = messages[-1]
    if not isinstance(last, ModelResponse):
        return ()
    parts = [part for part in last.parts if isinstance(part, ToolCallPart)]
    index = _current_index(parts, tool_call_id, tool_name, args)
    if index is None:
        return ()
    return tuple(_as_sibling(part) for part in parts[:index])


__all__ = [
    'ConcurrencyContext',
    'ConcurrencyGuard',
    'SiblingCall',
    'ToolConflict',
    'earlier_siblings',
]
