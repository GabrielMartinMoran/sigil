"""The ToolSummary value of the tooling layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Status = Literal['good', 'bad', 'neutral']


@dataclass(frozen=True, slots=True)
class ToolSummary:
    """What a tool says about a finished call, and how it went.

    It travels in the tool event and the UI paints it, so it is a value object and
    not a renderable: a presenter that returned a Rich `Text` would be deciding
    colors from the engine.
    """

    text: str
    status: Status = 'neutral'
