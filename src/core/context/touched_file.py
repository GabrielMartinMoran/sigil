"""The TouchedFile value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass

READ_TOOLS: frozenset[str] = frozenset({'read_file'})


@dataclass(frozen=True, slots=True)
class TouchedFile:
    """A file touched by the agent, with the trail of what was done to it."""

    path: str
    tools: tuple[str, ...]
    """Tools that touched it, in order of first appearance, without repeats."""

    @property
    def written(self) -> bool:
        return any(tool not in READ_TOOLS for tool in self.tools)

    def render(self) -> str:
        return f'- `{self.path}` ({", ".join(self.tools)})'
