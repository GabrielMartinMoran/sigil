"""Events emitted by a tool's wrapper.

An event describes a moment in the life of a call (it started, it finished fine,
it failed) with the minimum a sink needs to show it: name, args,
duration and a summary of the result or the error.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

"""Phase of a call: started, finished fine or failed."""

CURRENT_NODE_ID: ContextVar[str | None] = ContextVar('tool_event_node_id', default=None)
"""Execution owning tool events in the current async task or copied thread context."""

PREVIEW_LIMIT = 200
"""Characters kept in a one-line summary."""


def preview(value: Any, limit: int = PREVIEW_LIMIT) -> str:
    """One-line summary, safe for any value.

    Logging must never break a tool, so a ``__str__`` that blows up
    falls back to the object's ``repr`` instead of propagating the exception.
    """
    try:
        text = ' '.join(str(value).split())
    except Exception:  # noqa: BLE001 - logging must never break a tool
        text = object.__repr__(value)
    return text if len(text) <= limit else text[: limit - 1] + '…'
