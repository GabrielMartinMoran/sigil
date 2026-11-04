"""Incremental live rendering of the model responses.

The agent runs with `agent.run_sync(..., event_stream_handler=...)`, so
the agent loop runs to the end (executing every tool call) and,
as a side effect, hands us an event stream. This renderer consumes
those events and prints the thinking and text deltas as soon as they arrive.

Each model request emits its own block of events that starts with a
`PartStartEvent` with index 0. When we detect one, we close the previous line
and reset the state, so that each message (including the intermediate ones that
accompany a tool call) ends up on separate lines.
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
    """Prints the thinking and text deltas of a response live."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()
        self._printing_thinking = False
        self._printing_text = False

    async def handle_event(self, _ctx: Any, events: AsyncIterable[Any]) -> None:
        """Async handler for pydantic-ai's `event_stream_handler`."""
        async for event in events:
            self.render_event(event)

    def render_events(self, events: AsyncIterable[Any]) -> None:
        """Consumes an async event iterable in a sync context (for tests)."""
        import asyncio

        asyncio.run(self.handle_event(None, events))

    def finish(self) -> None:
        """Closes the current line when the whole turn ends."""
        self._finish()

    def render_event(self, event: Any) -> None:
        if isinstance(event, PartStartEvent):
            if event.index == 0:
                # New model response: we close the previous line.
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
