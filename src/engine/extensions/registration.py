"""Adapt external tool declarations to Sigil's normal tool registrar."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic_ai import RunContext

from sigil_tools import ToolContext
from src.engine.extensions.manager import ExtensionManager
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.compaction import GENERIC_COMPACTOR
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.outputs import register_output
from src.engine.tooling.registrar import ToolRegistrar


def register_extensions(
    target: Any,
    manager: ExtensionManager,
    *,
    catalog: ToolCatalog,
    logger: ToolLogger | None = None,
    context_factory: Callable[[RunContext[Any]], ToolContext] | None = None,
) -> None:
    """Register active external tools, rejecting collisions with existing tools."""
    for item in manager.tools:
        existing = catalog.get(item.name)
        if existing is not None and existing is not manager.registered_declarations.get(item.name):
            manager.errors.append(f'{item.directory}: tool {item.name!r} conflicts with an existing tool')
            continue
        declaration = item.declaration
        try:
            function, use_context = _adapt_context(declaration.function, context_factory)
            staging = ToolCatalog()
            registrar = ToolRegistrar(target, logger, staging)
            registrar.register(
                function,
                use_context=use_context,
                name=item.name,
                description=declaration.description,
                permission=declaration.permission,
                compaction=declaration.compaction or GENERIC_COMPACTOR,
                concurrency=declaration.concurrency,
                describe=declaration.describe,
                requires=declaration.requires,
            )
            staged = staging.get(item.name)
            assert staged is not None
            catalog.declare(
                item.name, permission=staged.permission, requires=staged.requires, compaction=staged.compaction
            )
            if declaration.output is not None:
                register_output(item.name, declaration.output)
            manager.registered_declarations[item.name] = catalog.get(item.name)
        except Exception as exc:  # noqa: BLE001 - invalid tools must not break other extensions
            manager.errors.append(f'{item.directory}: tool {item.name!r}: {type(exc).__name__}: {exc}')


def _adapt_context(
    function: Callable[..., Any],
    context_factory: Callable[[RunContext[Any]], ToolContext] | None,
) -> tuple[Callable[..., Any], bool]:
    hints = get_type_hints(function, include_extras=True)
    signature = inspect.signature(function)
    parameters = list(signature.parameters.values())
    contexts = [index for index, param in enumerate(parameters) if hints.get(param.name) is ToolContext]
    if not contexts:
        return function, False
    if contexts != [0] or parameters[0].kind not in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    ):
        raise TypeError('ToolContext must be the first positional parameter')
    if context_factory is None:
        raise ValueError('ToolContext requires a context factory')

    @functools.wraps(function)
    async def invoke(ctx: RunContext[Any], *args: Any, **kwargs: Any) -> Any:
        return await function(context_factory(ctx), *args, **kwargs)

    first = parameters[0]
    parameters[0] = first.replace(annotation=RunContext[Any])
    invoke.__dict__['__signature__'] = signature.replace(parameters=parameters)
    invoke.__annotations__ = {**hints, first.name: RunContext[Any]}
    return invoke, True
