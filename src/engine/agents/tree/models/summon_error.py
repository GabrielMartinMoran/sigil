"""What a ``summon`` was refused for, in a message the model can act on.

It is raised by the summoner and lands in the calling agent's history as a failed
tool result (see ``ToolErrorRecovery``), which is what lets the agent pick another
subagent or do the work itself instead of the turn dying on an exception.
"""

from __future__ import annotations


class SummonError(RuntimeError):
    """The subagent could not be started."""


__all__ = ['SummonError']
