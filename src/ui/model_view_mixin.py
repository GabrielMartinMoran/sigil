"""The active model as the chrome reads it: snapshot and effort in effect.

The side panel is the consumer: which model is active, and how hard is it
thinking right now. The widget that paints it stays dumb; the questions live
here so nothing else has to reach into the history.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelMessage, ModelSettingsDeltaPart

from src.engine.providers.domain.thinking import EFFORT_LEVELS, label
from src.engine.providers.snapshot import ModelSnapshot


class ModelViewMixin:
    """Reads the active model without touching the widgets.

    It expects the app to define ``_history`` and ``_model_service`` (in-process)
    or ``_client`` and ``_service_state`` (attached).
    """

    _history: list[ModelMessage]
    _client: Any
    _service_state: dict[str, Any] | None

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

    def accepts_images(self) -> bool | None:
        """Whether the active model can read an image, or ``None`` when unknown.

        In-process the snapshot answers. Attached, only the service knows: an
        empty local snapshot reports *no model configured*, which used to become
        the default ``True`` and let the composer accept a picture the provider
        would refuse after a whole turn had been spent.

        ``None`` means nobody can say. The composer refuses the image either way;
        what changes is that the sentence admits it does not know.
        """
        if getattr(self, '_client', None) is not None:
            return self._service_images()
        snapshot = self._model_snapshot()
        if not snapshot.is_configured:
            return True
        return snapshot.images

    def _service_model(self) -> dict[str, Any] | None:
        """The model the service projects, when it has projected one."""
        state = getattr(self, '_service_state', None) or {}
        model = state.get('model') if isinstance(state, dict) else None
        return model if isinstance(model, dict) else None

    def _service_images(self) -> bool | None:
        model = self._service_model()
        if model is None or not model.get('configured'):
            return None
        images = model.get('images')
        return images if isinstance(images, bool) else None

    def images_refusal(self) -> str:
        """Why an image cannot be attached, naming the way out."""
        if getattr(self, '_client', None) is not None:
            model = self._service_model() or {}
            name = str(model.get('model') or 'this model')
            if not model.get('configured'):
                return (
                    f'Sigil cannot tell whether {name} accepts images: the service has not reported a '
                    'configured model. Pick one with `/model`, or describe it in words.'
                )
            return f'{name} does not accept images. Pick a multimodal model with `/model`, or describe it in words.'
        snapshot = self._model_snapshot()
        return (
            f'{snapshot.model or "This model"} does not accept images. '
            'Pick a multimodal model with `/model`, or describe it in words.'
        )


def effort_label(model: ModelSnapshot, in_effect: str) -> str:
    """Readable effort, falling back to the selection when the history says nothing."""
    if in_effect and in_effect in EFFORT_LEVELS:
        return in_effect
    return str(model.thinking) if model.selection is not None else label('default')
