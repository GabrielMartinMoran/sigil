"""Estrategias de vista: qué historial ve el modelo en cada request.

La vista es **determinística**: no llama al modelo, no hace I/O y no muta el log.
Para el mismo `(mensajes, presupuesto)` devuelve siempre lo mismo. Eso la hace
testeable sin red y predecible en producción.

El log completo vive en la sesión y no se toca: la vista es una proyección.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.context.budget import ContextBudget
from src.engine.context.tokens import estimate_messages_tokens, estimate_tokens, tool_return_text
from src.engine.context.working_set import DEFAULT_WORKING_SET_LIMIT, render_working_set, touched_files

DEFAULT_KEEP_RECENT_TURNS = 3
"""Turnos que se mantienen intactos antes de empezar a recortar."""

DEFAULT_MAX_TOOL_RESULT_CHARS = 4_000
"""Tope de un tool result viejo. Los recientes no se tocan."""

DEFAULT_TOOL_RESULT_HEAD_CHARS = 400
"""Cuánto se conserva de un tool result truncado, para no perder la señal."""

PINNED_TOOLS: frozenset[str] = frozenset({'load_skill', 'read_skill_resource'})
"""Tools cuyo resultado es **instrucción**, no dato: nunca se truncan ni se compactan.

El cuerpo de un skill es lo que el agente tiene que seguir haciendo. Truncarlo a
un stub o colapsarlo en "load_skill → ok" le saca las instrucciones en medio de
la tarea. Medido: los skills instalados pesan entre 454 y 8.256 tokens.
"""


@dataclass(frozen=True, slots=True)
class ViewResult:
    """Resultado de proyectar el log a la vista del modelo."""

    messages: list[ModelMessage]
    estimated_tokens: int
    dropped_messages: int = 0
    dropped_thinking: int = 0
    truncated_results: int = 0
    compacted_turns: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.dropped_messages or self.dropped_thinking or self.truncated_results or self.compacted_turns)


class ContextStrategy(Protocol):
    """Proyecta el log de la sesión a lo que se manda al modelo."""

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult: ...


@dataclass(frozen=True, slots=True)
class PassthroughStrategy:
    """Manda el historial tal cual. Es el comportamiento previo a este módulo."""

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult:
        view = list(messages)
        return ViewResult(messages=view, estimated_tokens=estimate_messages_tokens(view))


def split_turns(messages: Sequence[ModelMessage]) -> list[list[ModelMessage]]:
    """Agrupa el historial en turnos, delimitados por el prompt del usuario.

    Un turno es autocontenido: prompt → respuestas → tool calls → tool returns.
    Por eso se puede descartar entero sin dejar un `ToolReturnPart` huérfano, que
    es lo que el provider rechaza con un 400.
    """
    turns: list[list[ModelMessage]] = []
    current: list[ModelMessage] = []
    for message in messages:
        starts_turn = isinstance(message, ModelRequest) and any(
            isinstance(part, UserPromptPart) for part in message.parts
        )
        if starts_turn and current:
            turns.append(current)
            current = []
        current.append(message)
    if current:
        turns.append(current)
    return turns


def _truncate_text(text: str, head_chars: int) -> str:
    omitted = len(text) - head_chars
    tokens = estimate_tokens(text)
    return f'{text[:head_chars]}\n\n[tool result truncated: {omitted:,} chars omitted, ~{tokens:,} tokens total]'


def _has_pinned_result(turn: Sequence[ModelMessage], pinned: frozenset[str]) -> bool:
    """Si el turno trae el resultado de una tool pineada (instrucciones)."""
    if not pinned:
        return False
    return any(
        isinstance(part, ToolReturnPart) and part.tool_name in pinned
        for message in turn
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


@dataclass(frozen=True, slots=True)
class WindowedStrategy:
    """Vista con ventana: recorta razonamiento, tool results viejos y turnos viejos.

    Orden de las palancas, de la más barata a la más destructiva:

    1. Dropear `ThinkingPart` — no aporta nada al siguiente paso y es lo más
       voluminoso. Verificado seguro con Anthropic vía OpenRouter.
    2. Truncar tool results de turnos viejos — el 80% del peso suele estar acá.
    3. Descartar turnos enteros, del más viejo al más nuevo.
    """

    keep_recent_turns: int = DEFAULT_KEEP_RECENT_TURNS
    max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS
    tool_result_head_chars: int = DEFAULT_TOOL_RESULT_HEAD_CHARS
    drop_thinking: bool = True
    keep_thinking: bool = False
    """Válvula de escape por provider: si una ruta exige round-trippear el
    razonamiento, se prende acá y no se dropea nada."""
    pinned_tools: frozenset[str] = PINNED_TOOLS
    """Tools cuyo resultado es instrucción y no se trunca nunca."""

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult:
        working = list(messages)
        dropped_thinking = 0
        truncated_results = 0

        if self.drop_thinking and not self.keep_thinking:
            working, dropped_thinking = _drop_thinking(working)

        turns = split_turns(working)
        recent_start = max(0, len(turns) - self.keep_recent_turns)

        working, truncated_results = _truncate_old_tool_results(
            turns,
            recent_start=recent_start,
            max_chars=self.max_tool_result_chars,
            head_chars=self.tool_result_head_chars,
            pinned_tools=self.pinned_tools,
        )

        dropped_messages = 0
        available = budget.available_for_history
        if available is not None:
            working, dropped_messages = _fit_to_budget(working, available, pinned_tools=self.pinned_tools)

        return ViewResult(
            messages=working,
            estimated_tokens=estimate_messages_tokens(working),
            dropped_messages=dropped_messages,
            dropped_thinking=dropped_thinking,
            truncated_results=truncated_results,
        )


def _drop_thinking(messages: list[ModelMessage]) -> tuple[list[ModelMessage], int]:
    """Saca los `ThinkingPart` de las respuestas del asistente."""
    result: list[ModelMessage] = []
    dropped = 0
    for message in messages:
        if not isinstance(message, ModelResponse):
            result.append(message)
            continue
        kept = [part for part in message.parts if not isinstance(part, ThinkingPart)]
        dropped += len(message.parts) - len(kept)
        result.append(message if len(kept) == len(message.parts) else dataclasses.replace(message, parts=kept))
    return result, dropped


def _truncate_old_tool_results(
    turns: list[list[ModelMessage]],
    *,
    recent_start: int,
    max_chars: int,
    head_chars: int,
    pinned_tools: frozenset[str] = frozenset(),
) -> tuple[list[ModelMessage], int]:
    """Trunca los tool results de los turnos viejos, dejando los recientes intactos.

    Mide el texto que el modelo realmente ve (`model_response_str()`), no el
    `str()` del objeto: las tools de Sigil devuelven modelos pydantic, y comparar
    contra `isinstance(content, str)` dejaba el truncado sin efecto.
    """
    result: list[ModelMessage] = []
    truncated = 0
    for index, turn in enumerate(turns):
        if index >= recent_start:
            result.extend(turn)
            continue
        for message in turn:
            if not isinstance(message, ModelRequest):
                result.append(message)
                continue
            parts = []
            changed = False
            for part in message.parts:
                if isinstance(part, ToolReturnPart) and part.tool_name not in pinned_tools:
                    text = tool_return_text(part)
                    if len(text) > max_chars:
                        parts.append(dataclasses.replace(part, content=_truncate_text(text, head_chars)))
                        truncated += 1
                        changed = True
                        continue
                parts.append(part)
            result.append(dataclasses.replace(message, parts=parts) if changed else message)
    return result, truncated


def _fit_to_budget(
    messages: list[ModelMessage],
    available: int,
    *,
    pinned_tools: frozenset[str] = frozenset(),
) -> tuple[list[ModelMessage], int]:
    """Descarta turnos enteros hasta entrar en el presupuesto.

    Prefiere descartar turnos sin resultados pineados: si hay que elegir entre
    perder un `read_file` viejo y perder las instrucciones de un skill, se pierde
    el `read_file`.
    """
    turns = split_turns(messages)
    dropped = 0

    def total() -> int:
        return estimate_messages_tokens([message for turn in turns for message in turn])

    while len(turns) > 1 and total() > available:
        victim = next(
            (index for index, turn in enumerate(turns[:-1]) if not _has_pinned_result(turn, pinned_tools)),
            None,
        )
        if victim is None:
            # Todo lo descartable trae instrucciones: se cae el más viejo igual.
            victim = 0
        dropped += len(turns[victim])
        turns.pop(victim)

    return [message for turn in turns for message in turn], dropped


def _clip(text: str, limit: int) -> str:
    text = ' '.join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + '…'


def _summarize_turn(turn: Sequence[ModelMessage], *, max_chars: int) -> str:
    """Resumen determinístico de un turno: qué pidió el usuario y qué se hizo.

    No llama al modelo. Conserva la intención y el rastro de tools; pierde los
    payloads, que es exactamente lo que se quiere perder.
    """
    prompt = ''
    actions: list[str] = []
    answer = ''

    for message in turn:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and not prompt:
                    content = part.content if isinstance(part.content, str) else str(part.content)
                    prompt = _clip(content, max_chars)
                elif isinstance(part, ToolReturnPart):
                    outcome = 'ok' if part.outcome == 'success' else part.outcome
                    actions.append(f'{part.tool_name} → {outcome}')
                elif isinstance(part, RetryPromptPart):
                    actions.append(f'{part.tool_name or "tool"} → retry')
        elif isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    actions.append(f'{part.tool_name}({_clip(str(part.args), 80)})')
                elif isinstance(part, TextPart) and part.content:
                    answer = _clip(part.content, max_chars)

    lines = []
    if prompt:
        lines.append(f'User: {prompt}')
    if actions:
        lines.append(f'Actions: {"; ".join(actions)}')
    if answer:
        lines.append(f'Assistant: {answer}')
    return '\n'.join(lines)


@dataclass(frozen=True, slots=True)
class CompactingStrategy:
    """Vista que colapsa los turnos viejos en un resumen determinístico.

    Los turnos recientes quedan intactos: el modelo suele necesitar los detalles
    exactos del último turno en el siguiente. Los viejos se reemplazan por un
    `CompactionPart` con la intención y el rastro de tools, sin payloads.

    Los turnos que traen el resultado de una tool pineada (un skill cargado) no
    se compactan: su contenido es instrucción, no dato.

    El resumen cierra con el **working set**: los archivos que el agente tocó,
    derivados del log completo. Sin eso, compactar borra el rastro de qué estaba
    editando y el agente vuelve a leer desde cero lo que ya tenía en la mano.

    El log no se toca: la compactación es una proyección, igual que la ventana.
    """

    keep_recent_turns: int = 2
    max_tool_result_chars: int = DEFAULT_MAX_TOOL_RESULT_CHARS
    tool_result_head_chars: int = DEFAULT_TOOL_RESULT_HEAD_CHARS
    drop_thinking: bool = True
    keep_thinking: bool = False
    max_summary_chars: int = 400
    pinned_tools: frozenset[str] = PINNED_TOOLS
    include_working_set: bool = True
    """Si el resumen cierra con los archivos tocados."""
    working_set_limit: int = DEFAULT_WORKING_SET_LIMIT
    """Cuántos archivos entran en el working set."""

    def view(self, messages: Sequence[ModelMessage], budget: ContextBudget) -> ViewResult:
        working = list(messages)
        dropped_thinking = 0
        if self.drop_thinking and not self.keep_thinking:
            working, dropped_thinking = _drop_thinking(working)

        turns = split_turns(working)
        recent_start = max(0, len(turns) - self.keep_recent_turns)
        old_turns = turns[:recent_start]
        recent_turns = turns[recent_start:]

        compacted = 0
        result: list[ModelMessage] = []
        pending: list[str] = []

        def flush() -> None:
            if pending:
                result.append(ModelRequest(parts=[CompactionPart(content='\n\n'.join(pending))]))
                pending.clear()

        for turn in old_turns:
            if _has_pinned_result(turn, self.pinned_tools):
                # Instrucciones cargadas: se conservan enteras y en su lugar.
                flush()
                result.extend(turn)
                continue
            text = _summarize_turn(turn, max_chars=self.max_summary_chars)
            if text:
                pending.append(text)
                compacted += 1

        if compacted and self.include_working_set:
            # El working set sale del log completo, no de los turnos viejos: lo
            # que el agente tocó hace un paso también es parte de lo que está
            # haciendo. Va al final del resumen para que quede lo más cerca
            # posible del turno en curso.
            working_set = render_working_set(touched_files(messages, limit=self.working_set_limit))
            if working_set:
                pending.append(working_set)
        flush()

        truncated = 0
        for index, turn in enumerate(recent_turns):
            # Los turnos recientes se truncan sólo si son viejos dentro de la ventana.
            if index < len(recent_turns) - 1:
                turn, count = _truncate_old_tool_results(
                    [turn],
                    recent_start=1,
                    max_chars=self.max_tool_result_chars,
                    head_chars=self.tool_result_head_chars,
                    pinned_tools=self.pinned_tools,
                )
                truncated += count
            result.extend(turn)

        dropped = 0
        available = budget.available_for_history
        if available is not None:
            result, dropped = _fit_keeping_summary(result, available, pinned_tools=self.pinned_tools)

        return ViewResult(
            messages=result,
            estimated_tokens=estimate_messages_tokens(result),
            dropped_messages=dropped,
            dropped_thinking=dropped_thinking,
            truncated_results=truncated,
            compacted_turns=compacted,
        )


def _fit_keeping_summary(
    messages: list[ModelMessage],
    available: int,
    *,
    pinned_tools: frozenset[str] = frozenset(),
) -> tuple[list[ModelMessage], int]:
    """Como `_fit_to_budget`, pero nunca descarta el resumen de compactación."""
    if estimate_messages_tokens(messages) <= available:
        return messages, 0

    head: list[ModelMessage] = []
    rest = list(messages)
    if rest and isinstance(rest[0], ModelRequest) and any(isinstance(part, CompactionPart) for part in rest[0].parts):
        head = [rest.pop(0)]

    turns = split_turns(rest)
    dropped = 0

    def total() -> int:
        return estimate_messages_tokens([*head, *(message for turn in turns for message in turn)])

    while len(turns) > 1 and total() > available:
        victim = next(
            (index for index, turn in enumerate(turns[:-1]) if not _has_pinned_result(turn, pinned_tools)),
            0,
        )
        dropped += len(turns[victim])
        turns.pop(victim)

    return [*head, *(message for turn in turns for message in turn)], dropped
