"""The only module that names pydantic-ai's `run_id`.

A turn is the span between the user sending a message and the agent finishing, and
steering stays inside it: `start_turn` with a turn already open defers to `steer`,
and `steer` calls `run.enqueue(...)` on the run that is open, so no new loop
starts. The library calls that span a run and stamps its `run_id` on every message
it produces — 1:1 with a turn.

That identifier is foreign vocabulary. It is contained here so the rest of the code
asks the domain, and the containment is verifiable:

    grep -rn 'run_id' src/engine --include='*.py' | grep -v 'agents/turn_map.py'

which must print nothing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage


@dataclass(frozen=True, slots=True)
class TurnSpan:
    """One turn, as found in a history: where it starts and how it is keyed."""

    index: int
    """Position of the turn's first message in the history."""
    turn_id: str
    """Deterministic id. A resumed write must not mint a second turn for a run it
    already stored, so the id is derived and not random."""
    library_run_id: str | None
    """pydantic-ai's identifier, or ``None`` for a message saved before it existed."""


def library_run_id(message: ModelMessage) -> str | None:
    """The foreign identifier of the turn ``message`` belongs to."""
    value = getattr(message, 'run_id', None)
    return value if isinstance(value, str) and value else None


def _turn_id(library_id: str | None, index: int) -> str:
    seed = f'turn:{library_id}' if library_id else f'turn:orphan:{index}'
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]


def turns_of(messages: Sequence[ModelMessage]) -> list[TurnSpan]:
    """The turns of a history, in order.

    A message with no identifier is **its own turn**, which is what the image cache
    already assumes for sessions saved before the library stamped one: ageing it
    with its neighbours would silently extend a window whose boundary is unknown.
    """
    spans: list[TurnSpan] = []
    seen: dict[str, int] = {}
    for index, message in enumerate(messages):
        current = library_run_id(message)
        key = current if current else f'orphan:{index}'
        if key in seen:
            continue
        seen[key] = index
        spans.append(TurnSpan(index=index, turn_id=_turn_id(current, index), library_run_id=current))
    return spans


def turn_id_of(messages: Sequence[ModelMessage], index: int) -> str:
    """The turn id of the message at ``index``."""
    current = library_run_id(messages[index])
    return _turn_id(current, index)


__all__ = ['TurnSpan', 'library_run_id', 'turn_id_of', 'turns_of']
