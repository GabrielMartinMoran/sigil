"""Scrollable chat history with auto-scroll anchored to the bottom."""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static


def _preview(value: Any, limit: int = 160) -> str:
    text = ' '.join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


def build_assistant_renderable(thinking: str, text: str):
    """Builds the message content: plain reasoning + Markdown text."""
    parts: list[object] = []
    if thinking:
        parts.append(Text(thinking, style='italic dim'))
    if text:
        parts.append(Markdown(text))
    if not parts:
        return Text('')
    if len(parts) == 1:
        return parts[0]
    return Group(*parts)


_RENDER_INTERVAL = 0.08  # seconds: Markdown re-render throttle while streaming


class AssistantBlock(Static):
    """One model response block (thinking + text).

    The Markdown is re-rendered at most every `_RENDER_INTERVAL` seconds so as
    not to re-parse it on every delta; a final flush is guaranteed with `finalize()`.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._thinking = ''
        self._text = ''
        self._dirty = False
        self._last_render = 0.0
        self._timer: Any = None

    def add_thinking(self, delta: str) -> None:
        self._thinking += delta
        self._schedule_refresh()

    def add_text(self, delta: str) -> None:
        self._text += delta
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_render

        if elapsed >= _RENDER_INTERVAL:
            self._flush()
            return

        self._dirty = True
        if self._timer is None:
            self._timer = self.set_timer(_RENDER_INTERVAL - elapsed, self._on_timer)

    def _on_timer(self) -> None:
        self._timer = None
        if self._dirty:
            self._flush()

    def _flush(self) -> None:
        self._dirty = False
        self._last_render = time.monotonic()
        self.update(build_assistant_renderable(self._thinking, self._text))

    def finalize(self) -> None:
        """Flushes the pending content right away (end of the turn)."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._flush()


class ChatLog(VerticalScroll):
    """Chat history with auto-scroll anchored to the bottom.

    It uses Textual's anchoring API (`anchor`): while the widget is anchored,
    the compositor keeps the view at the bottom on every layout, so the
    growing content (including streaming with a deferred flush) is followed
    automatically, with no manual scrolls and no timing issues.

    When scrolling up the anchor is released and it is restored only when the
    user goes back to the bottom, so whoever is reading history is respected.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current: AssistantBlock | None = None

    def on_mount(self) -> None:
        # Anchors the view to the bottom from the start.
        self.anchor()

    async def _append(self, widget: Static) -> None:
        # Anchoring takes care of the scroll; there is no need to force scroll_end.
        await self.mount(widget)

    async def add_user(self, text: str) -> None:
        self._current = None
        await self._append(Static(Text(f'› {text}', style='bold'), classes='user-msg'))

    async def add_notice(self, text: str, *, style: str = 'yellow') -> None:
        await self._append(Static(Text(text, style=style), classes='notice'))

    async def add_tool_call(self, name: str, args: Any) -> None:
        self._current = None
        await self._append(Static(Text(f'⚙ {name}({_preview(args)})', style='cyan'), classes='tool-call'))

    async def add_tool_result(self, name: str, output: Any, *, ok: bool = True) -> None:
        style = 'green' if ok else 'red'
        await self._append(Static(Text(f'↳ {_preview(output)}', style=style), classes='tool-result'))

    async def begin_response(self) -> None:
        """Marks the start of a new model response."""
        self._current = None

    async def _ensure_block(self) -> None:
        if self._current is None:
            self._current = AssistantBlock(classes='assistant-msg')
            await self.mount(self._current)

    async def add_thinking(self, delta: str) -> None:
        await self._ensure_block()
        assert self._current is not None
        self._current.add_thinking(delta)

    async def add_text(self, delta: str) -> None:
        await self._ensure_block()
        assert self._current is not None
        self._current.add_text(delta)

    def finalize(self) -> None:
        """Forces the pending render of every block (end of turn/history)."""
        for child in self.children:
            if isinstance(child, AssistantBlock):
                child.finalize()

    async def clear(self) -> None:
        self._current = None
        await self.remove_children()
        # After clearing, the new history follows the bottom again.
        self.anchor()

    async def render_history(self, messages: list[ModelMessage]) -> None:
        """Rebuilds the history from the pydantic-ai messages."""
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                        await self.add_user(part.content)
                    elif isinstance(part, ToolReturnPart):
                        ok = getattr(part, 'outcome', 'success') == 'success'
                        await self.add_tool_result(part.tool_name, part.content, ok=ok)
            elif isinstance(message, ModelResponse):
                for part in message.parts:
                    if isinstance(part, ThinkingPart) and part.content:
                        await self.add_thinking(part.content)
                    elif isinstance(part, TextPart) and part.content:
                        await self.add_text(part.content)
                    elif isinstance(part, ToolCallPart):
                        await self.add_tool_call(part.tool_name, part.args)
                self._current = None
