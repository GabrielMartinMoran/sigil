"""Lo que viaja en cada request además del historial: instrucciones y tools.

`ModelRequestParameters` es la fuente de verdad de lo que el provider serializa:
los `instruction_parts` ya resueltos (con nombre y si son dinámicos) y las
definiciones de tools. Medir desde acá, y no introspeccionando el agente, evita
que el diagnóstico mienta cuando una instrucción cambia entre requests.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.context.tokens import estimate_tokens


@dataclass(frozen=True, slots=True)
class InstructionRow:
    """Un bloque de instrucciones tal como se mandó."""

    name: str
    dynamic: bool
    """Si el contenido se recalcula en cada request (y por lo tanto puede romper el prefijo)."""
    tokens: int
    preview: str


@dataclass(frozen=True, slots=True)
class ToolRow:
    """Un schema de tool tal como se mandó."""

    name: str
    tokens: int
    deferred: bool = False
    """Si el schema quedó diferido (no viaja hasta que el historial lo revela)."""


@dataclass(frozen=True, slots=True)
class RequestSnapshot:
    """Costo fijo del último request: instrucciones y schemas de tools."""

    instructions: tuple[InstructionRow, ...] = ()
    tools: tuple[ToolRow, ...] = ()
    native_tools: int = 0
    """Tools nativas del provider (no viajan como schema JSON)."""

    captured: bool = False
    """Si el snapshot viene de un request real. En falso, los ceros no son una medición.

    Pasa en una sesión recién cargada con `/load`: hay historial, pero ningún
    request corrió en este proceso y por lo tanto no hay instrucciones ni tools
    observadas.
    """

    @property
    def instructions_tokens(self) -> int:
        return sum(row.tokens for row in self.instructions)

    @property
    def tools_tokens(self) -> int:
        return sum(row.tokens for row in self.tools)

    @property
    def dynamic_instructions(self) -> tuple[InstructionRow, ...]:
        """Bloques que se recalculan por request: candidatos a invalidar el caché."""
        return tuple(row for row in self.instructions if row.dynamic)


def _preview(text: str, limit: int = 120) -> str:
    collapsed = ' '.join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + '…'


def snapshot_request(parameters: object, *, preview_chars: int = 120) -> RequestSnapshot:
    """Arma el snapshot del request desde `ModelRequestParameters`.

    Sin parámetros no hubo request: devuelve un snapshot vacío y **no capturado**,
    para que el diagnóstico no confunda "no medí" con "no hay instrucciones".
    """
    if parameters is None:
        return RequestSnapshot()

    parts = getattr(parameters, 'instruction_parts', None) or ()
    instructions = tuple(
        InstructionRow(
            name=getattr(part, 'name', None) or '(base)',
            dynamic=bool(getattr(part, 'dynamic', False)),
            tokens=estimate_tokens(getattr(part, 'content', '') or ''),
            preview=_preview(getattr(part, 'content', '') or '', preview_chars),
        )
        for part in parts
    )

    definitions = getattr(parameters, 'function_tools', None) or ()
    tools = tuple(
        ToolRow(
            name=getattr(tool, 'name', '') or '?',
            tokens=estimate_tokens(_tool_payload(tool)),
            deferred=bool(getattr(tool, 'defer_loading', False)),
        )
        for tool in definitions
    )

    native = getattr(parameters, 'native_tools', None) or ()

    return RequestSnapshot(instructions=instructions, tools=tools, native_tools=len(native), captured=True)


def _tool_payload(tool: object) -> str:
    """Serialización aproximada del schema, igual que `overhead.measure_tool_definitions`."""
    import json

    payload = {
        'name': getattr(tool, 'name', ''),
        'description': getattr(tool, 'description', '') or '',
        'parameters': getattr(tool, 'parameters_json_schema', None) or {},
    }
    try:
        return json.dumps(payload, ensure_ascii=False, default=str)
    except TypeError, ValueError:
        return str(payload)
