"""Medición del costo fijo de un request: instrucciones y schemas de tools.

Se mide desde `ModelRequestParameters`, que es la fuente de verdad de lo que se
manda: los `instruction_parts` ya resueltos y las definiciones de tools que el
provider va a serializar. Nada de introspección del agente.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from src.engine.context.tokens import estimate_tokens


def measure_instruction_parts(parts: Iterable[Any] | None) -> int:
    """Tokens de los bloques de instrucciones resueltos."""
    if not parts:
        return 0
    return sum(estimate_tokens(getattr(part, 'content', '') or '') for part in parts)


def measure_tool_definitions(tools: Sequence[Any] | None) -> int:
    """Tokens de los schemas de tools, tal como los serializa el provider."""
    if not tools:
        return 0
    total = 0
    for tool in tools:
        payload = {
            'name': getattr(tool, 'name', ''),
            'description': getattr(tool, 'description', '') or '',
            'parameters': getattr(tool, 'parameters_json_schema', None) or {},
        }
        try:
            text = json.dumps(payload, ensure_ascii=False, default=str)
        except TypeError, ValueError:
            text = str(payload)
        total += estimate_tokens(text)
    return total


def measure_request_parameters(parameters: Any) -> tuple[int, int]:
    """``(instrucciones, tools)`` de un `ModelRequestParameters`."""
    if parameters is None:
        return 0, 0
    instructions = measure_instruction_parts(getattr(parameters, 'instruction_parts', None))
    tools = measure_tool_definitions(getattr(parameters, 'function_tools', None))
    return instructions, tools
