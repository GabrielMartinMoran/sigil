"""Historial de chat scrolleable con auto-scroll condicionado."""

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
    """Arma el contenido del mensaje: razonamiento plano + texto en Markdown."""
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


_RENDER_INTERVAL = 0.08  # segundos: throttle del re-render de Markdown en streaming


class AssistantBlock(Static):
    """Un bloque de respuesta del modelo (pensamiento + texto).

    El Markdown se re-renderiza como máximo cada `_RENDER_INTERVAL` segundos para
    no re-parsearlo en cada delta; se garantiza un flush final con `finalize()`.
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
        """Vuelca ya el contenido pendiente (fin del turno)."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self._flush()


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

    def finalize(self) -> None:
        """Fuerza el render pendiente de todos los bloques (fin de turno/historial)."""
        for child in self.children:
            if isinstance(child, AssistantBlock):
                child.finalize()

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
