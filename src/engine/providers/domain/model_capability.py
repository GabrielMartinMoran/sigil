"""What a model can do, as far as Sigil needs to know.

Everything here comes from the provider's catalogue, not from a guess, so the
side panel and the effort menu tell the truth for models pydantic-ai has never
heard of. When a catalogue cannot be reached, the capability degrades to
``context_window=None`` (the meter already renders ``?``) and an empty effort
list (the offer becomes "default only").
"""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.providers.domain.thinking import EFFORT_LEVELS, ThinkingEffort


@dataclass(frozen=True, slots=True)
class ModelCapability:
    """One model in a catalogue."""

    name: str
    """The id to send to the API."""

    display_name: str = ''
    context_window: int | None = None
    efforts: tuple[ThinkingEffort, ...] = ()
    """Levels the model accepts, in increasing order of reasoning."""

    dialect: str = 'chat-completions'
    """Wire dialect the provider serves this model with."""

    base_url: str | None = None
    """Per-model endpoint, when the catalogue publishes one (OpenCode does)."""

    supports_tools: bool = True

    supports_images: bool = True
    """Whether the model accepts image input.

    Defaults to true because that is what every current flagship does, and because
    a wrong ``False`` blocks a paste the provider would have accepted. A catalogue
    that declares ``text`` only sets it false, which refuses the paste before the
    turn is spent instead of failing with a 400 in the middle of one.
    """

    @property
    def label(self) -> str:
        return self.display_name or self.name

    @property
    def window_label(self) -> str:
        """``"200k"`` or ``"?"``."""
        if not self.context_window:
            return '?'
        if self.context_window >= 1_000_000:
            return f'{self.context_window // 1_000_000}M'
        return f'{self.context_window // 1000}k'

    @property
    def efforts_label(self) -> str:
        """``"low/medium/high"`` or ``"no effort control"``."""
        return '/'.join(self.efforts) if self.efforts else 'no effort control'

    def allows(self, effort: str) -> bool:
        """Whether the model accepts ``effort``. An empty list allows nothing."""
        return effort in self.efforts


def normalize_efforts(raw: object) -> tuple[ThinkingEffort, ...]:
    """Filters and orders whatever the catalogue said about efforts.

    Unknown levels are dropped, duplicates collapse and the result follows
    ``EFFORT_LEVELS`` order so every list in the UI reads least → most.
    """
    if not raw:
        return ()
    seen = {str(item).strip().lower() for item in raw}  # type: ignore[union-attr]
    return tuple(level for level in EFFORT_LEVELS if level in seen)


def accepts_image_input(modalities: object) -> bool:
    """Whether a catalogue's declared input modalities include an image.

    Two spellings for the same thing, and neither is stable: OpenCode publishes
    MIME types (``image/png``, ``image/jpeg``, ``image/webp``), OpenRouter
    publishes the bare family (``image``), and the next provider will publish
    whichever it likes. Matching the literal ``"image"`` is what once declared
    every multimodal OpenCode model blind, so the family prefix decides.

    A list that mentions no image is text-only and refuses the paste. Anything
    that is not a list is silence, and silence answers ``True``: refusing a paste
    a model would have accepted is the worse mistake.
    """
    if not isinstance(modalities, list):
        return True
    return any(isinstance(token, str) and token.strip().lower().split('/')[0] == 'image' for token in modalities)


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


EMPTY_CATALOG = ModelCatalog()
"""Used when an account has no known models at all."""
