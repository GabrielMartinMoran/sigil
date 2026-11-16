"""The SigilDeps value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SigilDeps:
    """What a run knows about the node it belongs to.

    It exists for the tools of the tree — ``summon``, ``send_message``,
    ``kill_subagent`` and the prompt blocks that list the neighbours. The root's
    run carries ``None``, so a tool resolves the root from the tree in effect
    instead of from a value the app would have to keep in step with `/load`.
    """

    node_id: str | None = None
