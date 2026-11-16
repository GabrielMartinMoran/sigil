"""Toolset that degrades instead of bringing the turn down.

An ``MCPToolset`` that cannot connect (downed server, nonexistent command,
handshake timeout) propagates the exception on enter, and it takes down the
agent's whole run. This wrapper catches it: the server contributes zero tools,
the rest of the conversation keeps going and the error stays available so
the UI can show it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Self

from pydantic_ai.toolsets import WrapperToolset


@dataclass
class ResilientToolset(WrapperToolset[Any]):
    """Wraps a toolset and absorbs its connection failures."""

    on_error: Callable[[str], None] | None = None
    error: str | None = field(default=None)

    def __post_init__(self) -> None:
        # An attribute, not a field: a framework `replace()` must not carry over the
        # entry state of the previous instance.
        self._entered = False

    @property
    def id(self) -> str | None:
        return self.wrapped.id

    async def __aenter__(self) -> Self:
        try:
            await self.wrapped.__aenter__()
        except Exception as exc:  # noqa: BLE001 - degrading is the point of this wrapper
            self._fail(exc)
        else:
            self._entered = True
        return self

    async def __aexit__(self, *args: object) -> bool | None:
        if not self._entered:
            # The wrapped `__aenter__` never completed: delegating would break its
            # internal entry count.
            return None
        self._entered = False
        try:
            return await self.wrapped.__aexit__(*args)
        except Exception:  # noqa: BLE001
            return None

    async def get_tools(self, ctx: Any) -> dict[str, Any]:
        if self.error is not None:
            return {}
        try:
            return await self.wrapped.get_tools(ctx)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return {}

    def _fail(self, exc: BaseException) -> None:
        if self.error is not None:
            return
        self.error = f'{type(exc).__name__}: {exc}'
        if self.on_error is not None:
            try:
                self.on_error(self.error)
            except Exception:  # noqa: BLE001, S110 - reporting must never break the run
                pass


__all__ = ['ResilientToolset']
