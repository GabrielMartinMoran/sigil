"""Emisión de eventos de tools hacia sinks inyectables.

El engine no conoce la UI: emite eventos a un ``ToolLogger`` y la UI enchufa un
sink que los escribe donde corresponda. Un sink puede ser sync o async, y un
fallo suyo nunca interrumpe la tool que lo disparó.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Sequence
from typing import Any

from src.engine.tooling.events import ToolCallEvent

ToolLogger = Callable[[ToolCallEvent], Any]
"""Sink de eventos. Puede ser sync o async; el retorno se ignora."""


async def emit_tool_event(logger: ToolLogger | None, event: ToolCallEvent) -> None:
    """Emite un evento a un sink, ignorando cualquier fallo del logging.

    Público para integraciones que no pasan por ``wrap_tool`` — por ejemplo las
    tools de un servidor MCP, que se ejecutan dentro de un toolset.
    """
    if logger is None:
        return
    try:
        result = logger(event)
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001, S110 - el logging nunca debe romper una tool
        pass


def emit_tool_event_sync(logger: ToolLogger | None, event: ToolCallEvent) -> None:
    """Emite desde una tool sync; programa los sinks async en el loop actual."""
    if logger is None:
        return
    try:
        result = logger(event)
        if inspect.isawaitable(result):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(result)
            else:
                loop.create_task(result)
    except Exception:  # noqa: BLE001, S110 - el logging nunca debe romper una tool
        pass


class ToolLoggerHub:
    """Fan-out de eventos de tools hacia uno o varios sinks.

    Es el punto de inyección entre engine y UI: el composition root crea el hub,
    se lo pasa a las tools y a la app, y la app agrega su sink al montar.
    """

    def __init__(self, sinks: Sequence[ToolLogger] = ()) -> None:
        self._sinks: list[ToolLogger] = list(sinks)

    def add_sink(self, sink: ToolLogger) -> None:
        if sink not in self._sinks:
            self._sinks.append(sink)

    def remove_sink(self, sink: ToolLogger) -> None:
        if sink in self._sinks:
            self._sinks.remove(sink)

    def __call__(self, event: ToolCallEvent) -> Any:
        return self.emit(event)

    async def emit(self, event: ToolCallEvent) -> None:
        for sink in tuple(self._sinks):
            await emit_tool_event(sink, event)
