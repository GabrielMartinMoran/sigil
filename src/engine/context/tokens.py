"""Medición y estimación de tokens de contexto.

Hay dos números distintos y no hay que confundirlos:

- **Medido**: los tokens de input que el provider reportó para el último request.
  Es ground truth de lo que se mandó, pero describe el pasado.
- **Proyectado**: el medido más una estimación de lo agregado desde entonces
  (tool results nuevos, prompt nuevo). Es lo que importa para saber si el próximo
  request entra en la ventana.

La estimación es ``chars / 4``. No pretende ser exacta: sirve para comparar
bloques entre sí y para proyectar un delta. El número exacto es el medido.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

CHARS_PER_TOKEN = 4
"""Heurística de estimación. El medido del provider manda cuando existe."""

# Mismo namespace que usa pydantic-ai para marcar el tamaño real de contexto de
# una respuesta cuyo usage suma varios requests (decodes largos de Google,
# compactaciones de Anthropic). Ver `pydantic_ai._context_size`.
_METADATA_KEY = '__pydantic_ai__'
_CONTEXT_INPUT_TOKENS_KEYS = ('context_input_tokens', 'initial_input_tokens')


def estimate_tokens(text: str) -> int:
    """Estimación de tokens de un texto. Vacío es cero, no uno."""
    if not text:
        return 0
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def _as_text(value: Any) -> str:
    """Convierte el contenido de una parte a texto plano, sin romperse."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    if isinstance(value, dict | list | tuple):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except TypeError, ValueError:
            return str(value)
    return str(value)


def part_text(part: Any) -> str:
    """Texto que aporta una parte al contexto, para estimar su costo."""
    if isinstance(part, SystemPromptPart | TextPart | ThinkingPart | CompactionPart):
        return part.content or ''
    if isinstance(part, UserPromptPart):
        content = part.content
        if isinstance(content, str):
            return content
        return ' '.join(_as_text(item) for item in content)
    if isinstance(part, ToolCallPart):
        return f'{part.tool_name} {_as_text(part.args)}'
    if isinstance(part, ToolReturnPart | RetryPromptPart):
        return f'{part.tool_name or ""} {tool_return_text(part)}'
    return _as_text(getattr(part, 'content', ''))


def tool_return_text(part: Any) -> str:
    """Texto que el modelo ve de un tool result.

    Las tools de Sigil devuelven modelos pydantic (`FileContent`, `PatchResult`,
    `LoadedSkill`), no strings. `model_response_str()` es la serialización exacta
    que viaja al provider; `str(content)` daría el repr de Python y mentiría
    sobre el tamaño.
    """
    model_response_str = getattr(part, 'model_response_str', None)
    if callable(model_response_str):
        # La estimación nunca debe romper el request.
        with contextlib.suppress(Exception):
            return model_response_str()
    return _as_text(getattr(part, 'content', ''))


def estimate_message_tokens(message: ModelMessage) -> int:
    """Estimación del costo de un mensaje completo."""
    total = sum(estimate_tokens(part_text(part)) for part in getattr(message, 'parts', ()))
    # Las instrucciones viajan en el request pero no son partes del mensaje.
    # Pueden ser un string ya renderizado o una lista de `InstructionPart`.
    instructions = getattr(message, 'instructions', None)
    if isinstance(instructions, str):
        total += estimate_tokens(instructions)
    elif instructions:
        total += sum(estimate_tokens(getattr(part, 'content', '') or '') for part in instructions)
    return total


def estimate_messages_tokens(messages: Sequence[ModelMessage]) -> int:
    return sum(estimate_message_tokens(message) for message in messages)


def _marked_context_input_tokens(response: ModelResponse) -> int | None:
    """Input tokens que miden el contexto, cuando el usage suma varios requests."""
    namespace = (response.metadata or {}).get(_METADATA_KEY)
    if not isinstance(namespace, dict):
        return None
    for key in _CONTEXT_INPUT_TOKENS_KEYS:
        value = namespace.get(key)
        if isinstance(value, int):
            return value
    return None


def response_context_tokens(response: ModelResponse) -> int:
    """Contexto que deja una respuesta: input (medido) más output generado.

    Espeja `pydantic_ai._context_size.context_tokens`, que es privado. Si cambia
    de semántica, el medido se degrada a ``usage.input_tokens + output_tokens``,
    que sigue siendo una cota razonable.
    """
    marked = _marked_context_input_tokens(response)
    input_tokens = response.usage.input_tokens if marked is None else marked
    return (input_tokens or 0) + (response.usage.output_tokens or 0)


def last_response_index(messages: Sequence[ModelMessage]) -> int | None:
    for index in range(len(messages) - 1, -1, -1):
        if isinstance(messages[index], ModelResponse):
            return index
    return None


def measured_tokens(messages: Sequence[ModelMessage]) -> int | None:
    """Tokens del último request que el provider reportó, o ``None`` si no hay."""
    index = last_response_index(messages)
    if index is None:
        return None
    response = messages[index]
    assert isinstance(response, ModelResponse)
    if response.usage.input_tokens is None and response.usage.output_tokens is None:
        return None
    return response_context_tokens(response)


def projected_tokens(messages: Sequence[ModelMessage]) -> int | None:
    """Estimación del próximo request: medido más lo agregado desde la última respuesta.

    Si todavía no hay respuesta (primer request del turno), proyecta el historial
    completo por estimación.
    """
    index = last_response_index(messages)
    if index is None:
        return estimate_messages_tokens(messages) or None
    measured = measured_tokens(messages)
    if measured is None:
        return estimate_messages_tokens(messages) or None
    return measured + estimate_messages_tokens(messages[index + 1 :])
