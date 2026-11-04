"""Registry of the skills loaded during the session.

The body of a loaded skill does not travel as a tool result: it is injected as an
instruction block. That makes it survive compaction and takes it out of the
history budget, where it would compete with the data.

This registry is the bridge between the `load_skill` tool (which feeds it) and the
instructions function (which reads it on every request).
"""

from __future__ import annotations


class LoadedSkills:
    """Names of the loaded skills, in load order and without repeats."""

    def __init__(self) -> None:
        self._names: list[str] = []

    def add(self, name: str) -> bool:
        """Registers a skill. Returns ``True`` if it was not already loaded."""
        if name in self._names:
            return False
        self._names.append(name)
        return True

    def names(self) -> tuple[str, ...]:
        return tuple(self._names)

    def reset(self) -> None:
        """Empties the registry. It is called when switching sessions."""
        self._names.clear()

    def __contains__(self, name: object) -> bool:
        return name in self._names

    def __len__(self) -> int:
        return len(self._names)

    def __bool__(self) -> bool:
        return bool(self._names)
