"""The structured checkpoint that replaces an older span in the model view.

This module plans checkpoints without calling a model:

- ``summary_boundary`` and ``summary_request`` are pure: they decide which messages a
  summary would cover, and they never split a tool exchange or a protected tail.
- ``summary_generator`` is the provider adapter that executes a planned request.

The transcript is data, never instructions: the prompt wraps it and says so.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Summary:
    """A checkpoint and the part key of the first message it does not replace."""

    text: str
    boundary: str


__all__ = ['Summary']
