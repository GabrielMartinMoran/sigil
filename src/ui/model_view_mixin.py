"""The active model as the chrome reads it: snapshot and effort in effect.

The side panel is the consumer: which model is active, and how hard is it
thinking right now. The widget that paints it stays dumb; the questions live
here so nothing else has to reach into the history.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelSettingsDeltaPart

from src.engine.providers.domain.thinking import EFFORT_LEVELS, label
from src.engine.providers.snapshot import ModelSnapshot


class ModelViewMixin:
    """Reads the active model without touching the widgets.

    It expects the app to define ``_history`` and ``_model_service``.
    """

    _history: list[ModelMessage]

    def effort_in_effect(self) -> str:
        """Effort actually in effect: the last one applied in the history, or the selected one."""
        for message in reversed(self._history):
            for part in reversed(getattr(message, 'parts', [])):
                if isinstance(part, ModelSettingsDeltaPart):
                    effort = part.settings.get('thinking')
                    if effort:
                        return str(effort)
        snapshot = self._model_snapshot()
        return str(snapshot.thinking)

    def _model_snapshot(self) -> ModelSnapshot:
        service = getattr(self, '_model_service', None)
        if service is None:
            return ModelSnapshot()
        return service.snapshot()


def effort_label(model: ModelSnapshot, in_effect: str) -> str:
    """Readable effort, falling back to the selection when the history says nothing."""
    if in_effect and in_effect in EFFORT_LEVELS:
        return in_effect
    return str(model.thinking) if model.selection is not None else label('default')
