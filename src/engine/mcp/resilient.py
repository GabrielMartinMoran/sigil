"""Toolset que degrada en vez de tumbar el turno.

Un ``MCPToolset`` que no puede conectar (server caído, comando inexistente,
timeout del handshake) propaga la excepción al entrar, y con ella se cae el
run entero del agente. Este wrapper la captura: el servidor aporta cero tools,
el resto de la conversación sigue andando y el error queda disponible para que
la UI lo muestre.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Self

from pydantic_ai.toolsets import WrapperToolset


@dataclass
class ResilientToolset(WrapperToolset[Any]):
    """Envuelve un toolset y absorbe sus fallos de conexión."""

    on_error: Callable[[str], None] | None = None
    error: str | None = field(default=None)

    def __post_init__(self) -> None:
        # Atributo, no campo: un `replace()` del framework no debe arrastrar el
        # estado de entrada de la instancia anterior.
        self._entered = False

    @property
    def id(self) -> str | None:
        return self.wrapped.id

    async def __aenter__(self) -> Self:
        try:
            await self.wrapped.__aenter__()
        except Exception as exc:  # noqa: BLE001 - degradar es el punto de este wrapper
            self._fail(exc)
        else:
            self._entered = True
        return self

    async def __aexit__(self, *args: object) -> bool | None:
        if not self._entered:
            # El `__aenter__` del wrapped nunca completó: delegar rompería su
            # conteo interno de entradas.
            return None
        self._entered = False
        try:
            return await self.wrapped.__aexit__(*args)
        except Exception:  # noqa: BLE001
            return None

    async def get_tools(self, ctx: Any) -> dict[str, Any]:
        if self.error is not None:
            return {}
        try:
            return await self.wrapped.get_tools(ctx)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return {}

    def _fail(self, exc: BaseException) -> None:
        if self.error is not None:
            return
        self.error = f'{type(exc).__name__}: {exc}'
        if self.on_error is not None:
            try:
                self.on_error(self.error)
            except Exception:  # noqa: BLE001, S110 - reportar nunca debe romper el run
                pass


__all__ = ['ResilientToolset']
