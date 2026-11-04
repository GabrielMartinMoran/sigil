"""Sink de eventos de tools que escribe en el chat de la UI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.message import Message

from src.engine.tooling.events import ToolCallEvent

if TYPE_CHECKING:
    from src.ui.app import SigilApp


class ToolLogMessage(Message):
    """Mensaje de Textual para actualizar el chat desde el logger de tools."""

    def __init__(self, event: ToolCallEvent) -> None:
        super().__init__()
        self.event = event


class ChatToolLogSink:
    """Traduce eventos del ``ToolLoggerHub`` a mensajes de la app.

    El sink corre en el contexto de la tool (posiblemente sync y en otro
    task/thread), así que no toca widgets directamente: encola un
    ``ToolLogMessage`` y la app lo procesa en su message pump.
    """

    def __init__(self, app: SigilApp) -> None:
        self._app = app

    def __call__(self, event: ToolCallEvent) -> None:
        self._app.post_message(ToolLogMessage(event))
