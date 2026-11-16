"""One reasoning effort the screen offers."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.providers.domain.thinking import ThinkingChoice


@dataclass(frozen=True, slots=True)
class EffortChoice:
    """What the user picked."""

    effort: ThinkingChoice
