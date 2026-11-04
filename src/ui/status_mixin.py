"""Barra de estado: modelo, esfuerzo y porcentaje de contexto proyectado."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelSettingsDeltaPart
from textual.app import ScreenStackError
from textual.css.query import NoMatches

from src.config_provider import ConfigProvider
from src.ui.efforts import EFFORTS
from src.ui.widgets.status_bar import StatusBar


class StatusMixin:
    """Pinta la barra de estado a partir del historial.

    Espera que la app defina ``_meter`` y ``_history``.
    """

    _meter: object
    _history: list[ModelMessage]

    def effort_in_effect(self) -> str:
        """Esfuerzo realmente en vigor: el último aplicado en el historial o el configurado."""
        for message in reversed(self._history):
            for part in reversed(getattr(message, 'parts', [])):
                if isinstance(part, ModelSettingsDeltaPart):
                    effort = part.settings.get('thinking')
                    if effort:
                        return str(effort)
        configured = ConfigProvider.REASONING_EFFORT
        return configured if configured in EFFORTS else 'default'

    def update_status(self, messages: Sequence[ModelMessage] | None = None) -> None:
        """La barra muestra el **proyectado**: lo que va a pesar el próximo request.

        El medido describe el request anterior; el proyectado suma lo agregado
        desde la última respuesta (tool results, prompt nuevo). Durante un turno
        se le pasa el historial del run, que va por delante de `self._history`.
        """
        snapshot = self._meter.snapshot(self._history if messages is None else messages)
        try:
            bar = self.query_one(StatusBar)
        except NoMatches, ScreenStackError:
            # La app puede no estar montada (tests, arranque). El cálculo no
            # depende de la UI, así que se descarta sólo el pintado.
            return
        bar.update_info(
            provider='openrouter',
            model=ConfigProvider.LLM_MODEL,
            effort=self.effort_in_effect(),
            percent=snapshot.projected_percent,
            context_length=snapshot.window,
        )
