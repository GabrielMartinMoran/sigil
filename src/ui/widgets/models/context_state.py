"""The context window as the sidebar paints it."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.context.meter.session_cost import SessionCost


@dataclass(frozen=True, slots=True)
class ContextState:
    """How much of the window the next request is going to take, and what it cost.

    ``used`` is the **projected** number, not the measured one: the panel answers
    "does the next request fit?", and the measured value describes a request that
    already happened.

    ``cost`` is the bill of the whole session, not of that request: it only moves
    forward, and it is the number a reader compares against their balance.
    """

    percent: float | None = None
    used: int | None = None
    window: int | None = None
    cost: SessionCost | None = None
