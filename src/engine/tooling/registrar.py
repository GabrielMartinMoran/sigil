"""Registro de tools en un agente de pydantic-ai, con logging y guardas.

``ToolRegistrar`` es el reemplazo de ``agent.tool_plain`` / ``agent.tool``: la
misma API, más el wrapper que loguea cada llamada y evalúa la guarda de
concurrencia declarada por la tool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from src.engine.tooling.concurrency import ConcurrencyGuard
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.wrapper import wrap_tool

if TYPE_CHECKING:
    from pydantic_ai import Agent


class ToolRegistrar:
    """Registra tools en un agente aplicando el wrapper de logging."""

    def __init__(self, agent: Agent, logger: ToolLogger | None = None) -> None:
        self.agent = agent
        self.logger = logger

    def tool(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        concurrency: ConcurrencyGuard | None = None,
        **tool_kwargs: Any,
    ) -> Any:
        """Decorador equivalente a ``agent.tool_plain`` con logging.

        ``concurrency`` declara cuándo la llamada no puede convivir con las
        anteriores de la misma respuesta del modelo.
        """
        return self._make(
            func, use_context=False, name=name, description=description, concurrency=concurrency, **tool_kwargs
        )

    def context_tool(
        self,
        func: Callable[..., Any] | None = None,
        *,
        name: str | None = None,
        description: str | None = None,
        concurrency: ConcurrencyGuard | None = None,
        **tool_kwargs: Any,
    ) -> Any:
        """Decorador equivalente a ``agent.tool`` (recibe ``RunContext``)."""
        return self._make(
            func, use_context=True, name=name, description=description, concurrency=concurrency, **tool_kwargs
        )

    def register(
        self,
        func: Callable[..., Any],
        *,
        use_context: bool = False,
        name: str | None = None,
        description: str | None = None,
        concurrency: ConcurrencyGuard | None = None,
        **tool_kwargs: Any,
    ) -> Callable[..., Any]:
        """Registra una función ya definida y devuelve el wrapper."""
        return self._make(
            func,
            use_context=use_context,
            name=name,
            description=description,
            concurrency=concurrency,
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
        **tool_kwargs: Any,
    ) -> Any:
        def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or target.__name__
            wrapped = wrap_tool(
                target,
                tool_name=tool_name,
                logger=self.logger,
                takes_ctx=use_context,
                concurrency=concurrency,
            )
            # Una tool con guarda necesita el contexto, así que se registra como
            # context tool aunque su función no lo pida.
            self._register(
                wrapped,
                use_context=use_context or concurrency is not None,
                name=name,
                description=description,
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
        **tool_kwargs: Any,
    ) -> None:
        options = {'name': name, 'description': description, **tool_kwargs}
        options = {key: value for key, value in options.items() if value is not None}
        register = self.agent.tool if use_context else self.agent.tool_plain
        if options:
            register(wrapped, **options)
        else:
            register(wrapped)


def register_tool(
    agent: Agent,
    func: Callable[..., Any] | None = None,
    *,
    logger: ToolLogger | None = None,
    use_context: bool = False,
    name: str | None = None,
    description: str | None = None,
    concurrency: ConcurrencyGuard | None = None,
    **tool_kwargs: Any,
) -> Any:
    """Atajo para registrar una tool suelta con logging."""
    registrar = ToolRegistrar(agent, logger)
    if func is None:

        def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
            return registrar.register(
                target,
                use_context=use_context,
                name=name,
                description=description,
                concurrency=concurrency,
                **tool_kwargs,
            )

        return decorator

    return registrar.register(
        func,
        use_context=use_context,
        name=name,
        description=description,
        concurrency=concurrency,
        **tool_kwargs,
    )
