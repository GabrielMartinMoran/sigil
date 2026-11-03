"""Render incremental en vivo de las respuestas del modelo.

El agente se ejecuta con `agent.run_sync(..., event_stream_handler=...)`, de modo
que el loop del agente corre hasta el final (ejecutando todas las tool calls) y,
como efecto colateral, nos entrega un stream de eventos. Este renderer consume
esos eventos e imprime los deltas de pensamiento y de texto apenas llegan.

Cada request al modelo emite su propio bloque de eventos que arranca con un
`PartStartEvent` de índice 0. Cuando detectamos uno, cerramos la línea anterior
y reseteamos el estado, de forma que cada mensaje (incluidos los intermedios que
acompañan a una tool call) quede en líneas separadas.
"""

from __future__ import annotations

from collections.abc import AsyncIterable
from typing import Any

from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
)
from rich.console import Console


class StreamRenderer:
    """Imprime en vivo los deltas de pensamiento y texto de una respuesta."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._printing_thinking = False
        self._printing_text = False

    async def handle_event(self, _ctx: Any, events: AsyncIterable[Any]) -> None:
        """Handler async para `event_stream_handler` de pydantic-ai."""
        async for event in events:
            self.render_event(event)

    def render_events(self, events: AsyncIterable[Any]) -> None:
        """Consume un iterable async de eventos en contexto sync (para tests)."""
        import asyncio

        asyncio.run(self.handle_event(None, events))

    def finish(self) -> None:
        """Cierra la línea en curso al terminar el turno completo."""
        self._finish()

    def render_event(self, event: Any) -> None:
        if isinstance(event, PartStartEvent):
            if event.index == 0:
                # Nueva respuesta del modelo: cerramos la línea anterior.
                self._finish()
            self._render_part(event.part)
        elif isinstance(event, PartDeltaEvent):
            delta = event.delta
            if isinstance(delta, TextPartDelta):
                if delta.content_delta:
                    self._write_text(delta.content_delta)
            elif isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                self._write_thinking(delta.content_delta)

    def _render_part(self, part: Any) -> None:
        if isinstance(part, ThinkingPart):
            if part.content:
                self._write_thinking(part.content)
        elif isinstance(part, TextPart) and part.content:
            self._write_text(part.content)

    def _write_thinking(self, delta: str) -> None:
        if self._printing_text:
            self._console.print()
            self._printing_text = False

        self._console.print(
            delta,
            end='',
            style='italic bright_black',
            markup=False,
            highlight=False,
            soft_wrap=True,
        )
        self._printing_thinking = True

    def _write_text(self, delta: str) -> None:
        if self._printing_thinking:
            self._console.print()
            self._printing_thinking = False

        self._console.print(delta, end='', markup=False, highlight=False, soft_wrap=True)
        self._printing_text = True

    def _finish(self) -> None:
        if self._printing_thinking or self._printing_text:
            self._console.print()
            self._printing_thinking = False
            self._printing_text = False
