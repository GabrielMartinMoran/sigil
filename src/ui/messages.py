"""Mensajes internos de la UI de Sigil."""

from __future__ import annotations

from textual.message import Message

from src.engine.agents.retry import RetryNotice


class RoadmapChanged(Message):
    """El store del roadmap cambió y el panel tiene que repintarse."""


class ModelRetryNotice(Message):
    """Aviso de que un fallo transitorio del modelo dispara un reintento."""

    def __init__(self, notice: RetryNotice) -> None:
        super().__init__()
        self.notice = notice
