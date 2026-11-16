"""What one model can do."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.providers.domain.thinking import ThinkingEffort


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
