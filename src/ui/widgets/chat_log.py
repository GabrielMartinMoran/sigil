"""Historial de chat scrolleable con auto-scroll condicionado."""

from __future__ import annotations

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
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static


def _preview(value: Any, limit: int = 160) -> str:
    text = ' '.join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


class AssistantBlock(Static):
    """Un bloque de respuesta del modelo (pensamiento + texto)."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(Text(''), **kwargs)
        self._thinking = ''
        self._text = ''

    def add_thinking(self, delta: str) -> None:
        self._thinking += delta
        self._refresh()

    def add_text(self, delta: str) -> None:
        self._text += delta
        self._refresh()

    def _refresh(self) -> None:
        content = Text()
        if self._thinking:
            content.append(self._thinking, style='italic dim')
            if self._text:
                content.append('\n\n')
        content.append(self._text)
        self.update(content)


class ChatLog(VerticalScroll):
    """Contenedor de mensajes; scrollea al final sólo si ya estaba al final."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._current: AssistantBlock | None = None

    def _at_bottom(self) -> bool:
        return self.is_vertical_scroll_end

    def _maybe_scroll(self, at_bottom: bool) -> None:
        if at_bottom:
            self.scroll_end(animate=False)

    async def _append(self, widget: Static) -> None:
        at_bottom = self._at_bottom()
        await self.mount(widget)
        self._maybe_scroll(at_bottom)

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
        """Marca el inicio de una nueva respuesta del modelo."""
        self._current = None

    async def _ensure_block(self) -> None:
        if self._current is None:
            self._current = AssistantBlock(classes='assistant-msg')
            await self.mount(self._current)

    async def add_thinking(self, delta: str) -> None:
        at_bottom = self._at_bottom()
        await self._ensure_block()
        assert self._current is not None
        self._current.add_thinking(delta)
        self._maybe_scroll(at_bottom)

    async def add_text(self, delta: str) -> None:
        at_bottom = self._at_bottom()
        await self._ensure_block()
        assert self._current is not None
        self._current.add_text(delta)
        self._maybe_scroll(at_bottom)

    async def clear(self) -> None:
        self._current = None
        await self.remove_children()

    async def render_history(self, messages: list[ModelMessage]) -> None:
        """Reconstruye el historial a partir de los mensajes de pydantic-ai."""
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
