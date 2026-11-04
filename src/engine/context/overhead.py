"""Measurement of the fixed cost of a request: instructions and tool schemas.

It is measured from `ModelRequestParameters`, which is the source of truth of what
gets sent: the already-resolved `instruction_parts` and the tool definitions the
provider is going to serialize. No agent introspection.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from src.engine.context.tokens import estimate_tokens


def measure_instruction_parts(parts: Iterable[Any] | None) -> int:
    """Tokens of the resolved instruction blocks."""
    if not parts:
        return 0
    return sum(estimate_tokens(getattr(part, 'content', '') or '') for part in parts)


def measure_tool_definitions(tools: Sequence[Any] | None) -> int:
    """Tokens of the tool schemas, as the provider serializes them."""
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
    """``(instructions, tools)`` of a `ModelRequestParameters`."""
    if parameters is None:
        return 0, 0
    instructions = measure_instruction_parts(getattr(parameters, 'instruction_parts', None))
    tools = measure_tool_definitions(getattr(parameters, 'function_tools', None))
    return instructions, tools
