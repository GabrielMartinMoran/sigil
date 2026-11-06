"""Turn ids are scoped by execution, which is what makes a fork possible.

A turn id used to be derived from the library's identifier alone. Two sessions that
share a transcript prefix — which is exactly what a fork is — would then mint the
same ids, and `turns.id` is a primary key: the fork would collide with the original.

The scope is the execution, not the session, because that is where the determinism
matters: "a resumed write must not mint a second turn for a run it already stored" is
a statement about one transcript, and two executions are two transcripts.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage


def library_run_id(message: ModelMessage) -> str | None:
    """The foreign identifier of the turn ``message`` belongs to."""
    value = getattr(message, 'run_id', None)
    return value if isinstance(value, str) and value else None


def turn_id_of(messages: Sequence[ModelMessage], index: int, *, scope: str = '') -> str:
    """The turn id of the message at ``index``, inside ``scope``.

    ``scope`` is the execution id. Empty is allowed for a history that is being read
    without an execution yet — a test, or a preview — and it is stable as long as the
    caller is.
    """
    current = library_run_id(messages[index])
    seed = f'turn:{scope}:{current}' if current else f'turn:{scope}:orphan:{index}'
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()[:32]


__all__ = ['library_run_id', 'turn_id_of']
