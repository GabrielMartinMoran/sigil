"""Transport message of the terminal."""

from __future__ import annotations

from dataclasses import dataclass

from src.core.commands.command_owner import CommandOwner
from src.ui.transport_state import TransportState


@dataclass(frozen=True)
class Transport:
    """One command's way out of this terminal.

    ``reason`` explains a row that does not run here, in the words the user reads:
    it names what is missing, not which cut is missing it. It is empty exactly for
    the rows that run here, because they have nothing to explain.
    """

    name: str
    owner: CommandOwner
    state: TransportState
    route: str | None = None
    reason: str = ''
