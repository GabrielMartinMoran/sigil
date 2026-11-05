"""Capability that hides the tools the current model cannot use.

A route that does not read images has no use for `view_image`. Advertising it anyway
is worse than useless: it occupies the schema on every request, and it invites a call
that fails at the provider in the middle of a turn. The tool has to be *absent*, not
merely refused — a refusal is something the model plans around.

The declaration lives on the tool (`requires='image_input'`) and travels as metadata,
so this capability never needs a list of tool names: it compares what each definition
declares against what the model supports right now.

It reads the model per request, not at construction: `/model` swaps the route
mid-session and the tool set has to follow without rebuilding the agent.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability

from src.engine.tooling.registrar import REQUIRES_METADATA_KEY

if TYPE_CHECKING:
    from pydantic_ai.messages import ToolCallPart
    from pydantic_ai.tools import RunContext, ToolDefinition

IMAGE_INPUT = 'image_input'
"""Feature name for "this route can read images"."""


@dataclass
class ModelFeatureGate(AbstractCapability[Any]):
    """Drops the tools whose required features the model does not have."""

    _: KW_ONLY

    supports: Callable[[str], bool]
    """Answers whether the current model has a feature, by name."""
    id: str | None = 'model_features'

    async def prepare_tools(
        self,
        ctx: RunContext[Any],
        tool_defs: list[ToolDefinition],
    ) -> list[ToolDefinition]:
        del ctx
        return [tool_def for tool_def in tool_defs if self._usable(tool_def)]

    async def before_tool_execute(
        self,
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        del ctx
        if self._usable(tool_def):
            return args
        from pydantic_ai.exceptions import ToolFailed

        raise ToolFailed(
            f'The active model cannot use `{tool_def.name}`: it needs '
            f'{", ".join(sorted(_required(tool_def)))}. Tell the user instead of retrying.'
        )

    def _usable(self, tool_def: ToolDefinition) -> bool:
        return all(self.supports(feature) for feature in _required(tool_def))


def _required(tool_def: ToolDefinition) -> frozenset[str]:
    """The features a tool definition declares it needs."""
    declared = (tool_def.metadata or {}).get(REQUIRES_METADATA_KEY)
    if not isinstance(declared, list | tuple | set | frozenset):
        return frozenset()
    return frozenset(str(item) for item in declared)


__all__ = ['IMAGE_INPUT', 'ModelFeatureGate']
