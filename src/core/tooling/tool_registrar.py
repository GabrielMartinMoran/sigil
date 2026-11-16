"""The ToolRegistrar value of the tooling layer."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.core.tooling.concurrency import ConcurrencyGuard
from src.core.tooling.logging import ToolLogger
from src.core.tooling.outputs import OutputPresenter, register_output
from src.core.tooling.presentation import Describe
from src.core.tooling.tool_catalog import ToolCatalog
from src.core.tooling.tool_compactor import ToolCompactor
from src.core.tooling.tool_permission import ToolPermission
from src.core.tooling.wrapper import wrap_tool

if TYPE_CHECKING:
    from pydantic_ai import Agent


REQUIRES_METADATA_KEY = 'sigil_requires'


def _as_requirements(requires: str | tuple[str, ...] | None) -> frozenset[str]:
    """Normalizes the declaration to a set of feature names."""
    if requires is None:
        return frozenset()
    if isinstance(requires, str):
        return frozenset({requires})
    return frozenset(requires)


class ToolRegistrar:
    """Registers tools on an agent applying the logging wrapper."""

    def __init__(
        self,
        agent: Agent,
        logger: ToolLogger | None = None,
        catalog: ToolCatalog | None = None,
    ) -> None:
        self.agent = agent
        self.logger = logger
        # Shared across registrars on purpose: every tool of the agent ends up in the
        # same catalogue, and the prompt reads it.
        self.catalog = catalog if catalog is not None else ToolCatalog()

    def tool(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        concurrency: ConcurrencyGuard | None = None,
        permission: ToolPermission | None = None,
        requires: str | tuple[str, ...] | None = None,
        describe: Describe | None = None,
        output: OutputPresenter | None = None,
        compaction: ToolCompactor | None = None,
        **tool_kwargs: Any,
    ) -> Any:
        """Decorator equivalent to ``agent.tool_plain`` with logging.

        ``concurrency`` declares when the call cannot coexist with the
        previous ones of the same model response. ``permission`` declares what
        the tool is for the agent profile, which is what lets a profile deny a
        whole group or a specific path instead of only the tool by name.
        ``describe`` is how the tool summarizes its own result in one line for the
        transcript; see ``presentation``. ``output`` formats normalized result
        data for the expanded human view, including session replay.
        """
        return self._make(
            func,
            use_context=False,
            name=name,
            description=description,
            concurrency=concurrency,
            permission=permission,
            requires=requires,
            describe=describe,
            output=output,
            compaction=compaction,
            **tool_kwargs,
        )

    def context_tool(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        concurrency: ConcurrencyGuard | None = None,
        permission: ToolPermission | None = None,
        requires: str | tuple[str, ...] | None = None,
        describe: Describe | None = None,
        output: OutputPresenter | None = None,
        compaction: ToolCompactor | None = None,
        **tool_kwargs: Any,
    ) -> Any:
        """Decorator equivalent to ``agent.tool`` (it receives a ``RunContext``)."""
        return self._make(
            func,
            use_context=True,
            name=name,
            description=description,
            concurrency=concurrency,
            permission=permission,
            requires=requires,
            describe=describe,
            output=output,
            compaction=compaction,
            **tool_kwargs,
        )

    def register(
        self,
        func: Callable[..., Any],
        *,
        use_context: bool = False,
        name: str | None = None,
        description: str | None = None,
        concurrency: ConcurrencyGuard | None = None,
        permission: ToolPermission | None = None,
        requires: str | tuple[str, ...] | None = None,
        describe: Describe | None = None,
        output: OutputPresenter | None = None,
        compaction: ToolCompactor | None = None,
        **tool_kwargs: Any,
    ) -> Callable[..., Any]:
        """Registers an already defined function and returns the wrapper."""
        return self._make(
            func,
            use_context=use_context,
            name=name,
            description=description,
            concurrency=concurrency,
            permission=permission,
            requires=requires,
            describe=describe,
            output=output,
            compaction=compaction,
            **tool_kwargs,
        )

    def _make(
        self,
        func: Callable[..., Any] | None,
        *,
        use_context: bool,
        name: str | None,
        description: str | None,
        concurrency: ConcurrencyGuard | None = None,
        permission: ToolPermission | None = None,
        requires: str | tuple[str, ...] | None = None,
        describe: Describe | None = None,
        output: OutputPresenter | None = None,
        compaction: ToolCompactor | None = None,
        **tool_kwargs: Any,
    ) -> Any:
        def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or target.__name__
            if output is not None:
                register_output(tool_name, output)
            wrapped = wrap_tool(
                target,
                tool_name=tool_name,
                logger=self.logger,
                takes_ctx=use_context,
                concurrency=concurrency,
                describe=describe,
            )
            needed = _as_requirements(requires)
            self.catalog.declare(tool_name, permission=permission, requires=needed, compaction=compaction)
            metadata = dict(permission.metadata()) if permission is not None else {}
            if needed:
                metadata[REQUIRES_METADATA_KEY] = sorted(needed)
            # A guarded tool needs the context, so it is registered as a
            # context tool even if its function does not ask for it.
            self._register(
                wrapped,
                use_context=use_context or concurrency is not None,
                name=name or tool_name,
                description=description,
                metadata=metadata or None,
                **tool_kwargs,
            )
            return wrapped

        return decorator(func) if func is not None else decorator

    def _register(
        self,
        wrapped: Callable[..., Any],
        *,
        use_context: bool,
        name: str | None,
        description: str | None,
        metadata: dict[str, Any] | None = None,
        **tool_kwargs: Any,
    ) -> None:
        options = {'name': name, 'description': description, 'metadata': metadata, **tool_kwargs}
        options = {key: value for key, value in options.items() if value is not None}
        register = self.agent.tool if use_context else self.agent.tool_plain
        if options:
            register(wrapped, **options)
        else:
            register(wrapped)
