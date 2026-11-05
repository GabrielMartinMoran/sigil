"""Filters, prefix and controlled degradation of an MCP toolset."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic_ai.toolsets import AbstractToolset

from src.engine.mcp.config_models.server_spec import MCPServerSpec
from src.engine.mcp.resilient import ResilientToolset
from src.engine.mcp.server.naming import includes

if TYPE_CHECKING:
    from pydantic_ai.mcp import MCPToolset


def _filter(spec: MCPServerSpec) -> Callable[[Any, Any], bool]:
    def predicate(_ctx: Any, tool_def: Any) -> bool:
        return includes(spec, tool_def.name)

    return predicate


def wrap_toolset(spec: MCPServerSpec, toolset: MCPToolset, on_error: Callable[[str], None]) -> AbstractToolset:
    """Applies filters, prefix and controlled degradation to the toolset."""
    wrapped: AbstractToolset = toolset
    if spec.config.include_tools or spec.config.exclude_tools:
        wrapped = wrapped.filtered(_filter(spec))
    if spec.tool_prefix:
        wrapped = wrapped.prefixed(spec.tool_prefix)
    return ResilientToolset(wrapped, on_error=on_error)
