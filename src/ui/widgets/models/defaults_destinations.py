"""The files a defaults write can reach."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from src.core.agents.config_scope import ConfigScope


class DefaultsDestinations(Protocol):
    """Where a write can go, which is all the screen asks about a destination.

    The screen paints one line about the target file and refuses to clear an entry a
    file does not set, so it needs four facts: which files exist, where each one is,
    whether ``SIGIL_CONFIG`` replaced the pair, and what could not be parsed.

    It is a protocol and not `ModelDefaultsState` because the two modes answer it from
    different places. In process the state is read from the files; attached it is the
    service's projection, and the screen must not reach for the engine's reader to
    paint what the service already resolved. What is deliberately *not* here is
    ``effective``/``invalid``/``sources``: those are precedence, and each row arrives
    already carrying the answer the engine gave.
    """

    @property
    def destinations(self) -> tuple[ConfigScope, ...]:
        """The files a write may go to, in precedence order."""

    @property
    def fixed(self) -> Path | None:
        """The single explicit path, when ``SIGIL_CONFIG`` replaced the pair."""

    @property
    def errors(self) -> tuple[str, ...]:
        """What the files could not parse, in the order the screen lists it."""

    def path_for(self, scope: ConfigScope) -> Path:
        """The file ``scope`` names."""
