"""Tool registration on a pydantic-ai agent, with logging and guards.

``ToolRegistrar`` is the replacement for ``agent.tool_plain`` / ``agent.tool``: the
same API, plus the wrapper that logs every call and evaluates the concurrency
guard declared by the tool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.core.tooling.concurrency import ConcurrencyGuard
from src.core.tooling.logging import ToolLogger
from src.core.tooling.tool_permission import ToolPermission
from src.core.tooling.tool_registrar import ToolRegistrar

"""Key of the model features a tool needs, inside ``ToolDefinition.metadata``."""


if TYPE_CHECKING:
    from pydantic_ai import Agent


def register_tool(
    agent: Agent,
    func: Callable[..., Any] | None = None,
    *,
    logger: ToolLogger | None = None,
    use_context: bool = False,
    name: str | None = None,
    description: str | None = None,
    concurrency: ConcurrencyGuard | None = None,
    permission: ToolPermission | None = None,
    **tool_kwargs: Any,
) -> Any:
    """Shorthand to register a standalone tool with logging."""
    registrar = ToolRegistrar(agent, logger)
    if func is None:

        def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
            return registrar.register(
                target,
                use_context=use_context,
                name=name,
                description=description,
                concurrency=concurrency,
                permission=permission,
                **tool_kwargs,
            )

        return decorator

    return registrar.register(
        func,
        use_context=use_context,
        name=name,
        description=description,
        concurrency=concurrency,
        permission=permission,
        **tool_kwargs,
    )
