"""The AgentRunModel value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.models import Model


@dataclass(frozen=True, slots=True)
class AgentRunModel:
    """The concrete model and reasoning settings captured for one execution."""

    model: Model | None
    settings: dict[str, Any] | None
    label: str
    window: int | None = None
    """Context window of the model this execution sends to, when anything knows it.

    Read from the built model first and from the catalogue second, because the
    catalogue is a network resource and a cold or offline start describes nothing.
    ``None`` means no source knew it: a run that overrides the model is then refused
    rather than sent blind, and one that does not keeps the window the meter holds.
    """
    images: bool = True
    provider: str | None = None
