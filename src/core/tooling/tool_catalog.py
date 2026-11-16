"""The ToolCatalog value of the tooling layer."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from src.core.tooling.tool_compactor import ToolCompactor
from src.core.tooling.tool_declaration import ToolDeclaration
from src.core.tooling.tool_permission import ToolPermission


@dataclass(slots=True)
class ToolCatalog:
    """Every tool registered on the agent, in registration order.

    It is a record of what exists, not a registry of what is enabled: the filters read
    it, so it must hold the denied tools too.
    """

    _tools: dict[str, ToolDeclaration] = field(default_factory=dict)

    def declare(
        self,
        name: str,
        *,
        permission: ToolPermission | None = None,
        requires: Iterable[str] = (),
        compaction: ToolCompactor | None = None,
    ) -> None:
        self._tools[name] = ToolDeclaration(
            name=name,
            permission=permission,
            requires=frozenset(requires),
            compaction=compaction,
        )

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> ToolDeclaration | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def declarations(self) -> tuple[ToolDeclaration, ...]:
        return tuple(self._tools.values())

    def requires(self, name: str) -> frozenset[str]:
        declaration = self._tools.get(name)
        return frozenset() if declaration is None else declaration.requires

    def compactor(self, name: str) -> ToolCompactor | None:
        """The compactor the tool declared, if any."""
        declaration = self._tools.get(name)
        return None if declaration is None else declaration.compaction

    def uncovered(self) -> tuple[str, ...]:
        """Tools registered without a compactor.

        Every tool this project owns declares one, so a name here is a missing
        declaration, not a tool from outside: the agent factory refuses to build with
        a non-empty answer.
        """
        return tuple(name for name, declaration in self._tools.items() if declaration.compaction is None)
