"""What each tool needs to exist, declared where the tool is registered.

A tool is not free: it occupies the schema the provider serializes on every request,
and the model plans around it. Two things decide whether it belongs in a request:

- **The model.** A route that cannot read images has no use for `view_image`, and
  advertising it invites a call that fails.
- **The agent profile.** A profile that denies `read` must not be told about
  `read_file` — the tool is already filtered out of the definitions, and a prompt that
  still describes it teaches the model to plan around something it cannot do.

Both answers need the same catalogue, so it is built once while the tools are
registered: the name of each tool, the permission it declared, and the model feature
it requires.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from src.engine.tooling.compaction import ToolCompactor
from src.engine.tooling.permission import ToolPermission


@dataclass(frozen=True, slots=True)
class ToolDeclaration:
    """One tool: what it needs, what it is allowed to do, and what it keeps."""

    name: str
    permission: ToolPermission | None = None
    requires: frozenset[str] = frozenset()
    """Model features the tool needs. Empty means every route can use it."""

    compaction: ToolCompactor | None = None
    """How the tool's arguments and results are reduced when history is compacted.

    ``None`` means the tool did not declare one, which is allowed only for tools this
    project does not own (MCP servers, external tools). `uncovered()` is what turns
    that into a failure for our own.
    """


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
