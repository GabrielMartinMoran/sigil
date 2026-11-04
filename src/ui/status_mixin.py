"""Status bar: model, effort and projected context percentage."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelSettingsDeltaPart
from textual.app import ScreenStackError
from textual.css.query import NoMatches

from src.config_provider import ConfigProvider
from src.ui.efforts import EFFORTS
from src.ui.widgets.status_bar import StatusBar


class StatusMixin:
    """Paints the status bar from the history.

    It expects the app to define ``_meter`` and ``_history``.
    """

    _meter: object
    _history: list[ModelMessage]

    def effort_in_effect(self) -> str:
        """Effort actually in effect: the last one applied in the history, or the configured one."""
        for message in reversed(self._history):
            for part in reversed(getattr(message, 'parts', [])):
                if isinstance(part, ModelSettingsDeltaPart):
                    effort = part.settings.get('thinking')
                    if effort:
                        return str(effort)
        configured = ConfigProvider.REASONING_EFFORT
        return configured if configured in EFFORTS else 'default'

    def update_status(self, messages: Sequence[ModelMessage] | None = None) -> None:
        """The bar shows the **projected** number: what the next request will weigh.

        The measured one describes the previous request; the projected one adds what was added
        since the last response (tool results, new prompt). During a turn
        it is given the run history, which runs ahead of `self._history`.
        """
        snapshot = self._meter.snapshot(self._history if messages is None else messages)
        try:
            bar = self.query_one(StatusBar)
        except NoMatches, ScreenStackError:
            # The app may not be mounted (tests, startup). The calculation does not
            # depend on the UI, so only the painting is skipped.
            return
        bar.update_info(
            provider='openrouter',
            model=ConfigProvider.LLM_MODEL,
            effort=self.effort_in_effect(),
            percent=snapshot.projected_percent,
            context_length=snapshot.window,
        )
