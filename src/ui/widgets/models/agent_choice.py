"""The agent a picker offers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentChoice:
    """The profile the user picked."""

    name: str
