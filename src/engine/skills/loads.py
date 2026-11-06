"""Which skills each agent of the session has loaded.

A subagent does not inherit the parent's loaded skills, and the parent does not
inherit its. That is not a detail of bookkeeping: a loaded skill injects its whole
body into the instructions, so a shared registry would put the Lead's design
guidance into an engineer's prompt, spend its budget on it, and make two agents
that are supposed to read the same task differently agree by accident.

The registry is keyed by node, with the root's entry under ``None``: the root is the
one node whose id is not known when the session is built, and every caller already
passes ``None`` for it.

It also has to survive `/load`, so sessions do not share: the store is rebuilt
whenever the session changes, exactly like the tree.
"""

from __future__ import annotations

from typing import Any

from src.engine.skills.loaded import LoadedSkills


class SkillLoads:
    """One ``LoadedSkills`` per agent of the session."""

    def __init__(self) -> None:
        self._by_node: dict[str | None, LoadedSkills] = {}

    def for_node(self, node_id: str | None) -> LoadedSkills:
        """The registry of one node, created on first use."""
        registry = self._by_node.get(node_id)
        if registry is None:
            registry = LoadedSkills()
            self._by_node[node_id] = registry
        return registry

    @property
    def root(self) -> LoadedSkills:
        """The agent the user talks to."""
        return self.for_node(None)

    def names(self, node_id: str | None) -> tuple[str, ...]:
        return self.for_node(node_id).names()

    def reset(self) -> None:
        """Forgets every load. Called when the session changes."""
        self._by_node.clear()

    def to_dict(self) -> dict[str, list[str]]:
        """The loaded names per node, ready to travel with the session."""
        return {('' if key is None else key): list(value.names()) for key, value in self._by_node.items() if value}

    def restore(self, data: Any) -> None:
        """Rebuilds from what the session kept. A broken entry is skipped."""
        self._by_node.clear()
        if not isinstance(data, dict):
            return
        for key, names in data.items():
            if not isinstance(names, list):
                continue
            registry = self.for_node(key or None)
            for name in names:
                if isinstance(name, str) and name:
                    registry.add(name)


__all__ = ['SkillLoads']
