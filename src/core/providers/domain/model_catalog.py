"""The ModelCatalog value of the providers layer."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.providers.domain.model_capability import ModelCapability


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    """The models of one account, plus how reliable the list is."""

    models: tuple[ModelCapability, ...] = ()
    source: str = 'static'
    """``"remote"`` when it came from the provider, ``"static"`` for the fallback."""

    error: str | None = None
    """Why the remote list could not be fetched, if it could not."""

    def find(self, name: str) -> ModelCapability | None:
        for model in self.models:
            if model.name == name:
                return model
        return None

    def with_models(self, models: tuple[ModelCapability, ...], *, source: str) -> ModelCatalog:
        return ModelCatalog(models=models, source=source, error=None)

    def failed(self, error: str) -> ModelCatalog:
        return ModelCatalog(models=self.models, source='static', error=error)

    @property
    def is_empty(self) -> bool:
        return not self.models
