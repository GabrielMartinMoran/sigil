"""Vista que colapsa los turnos viejos en un resumen determinístico."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai.messages import CompactionPart, ModelMessage, ModelRequest

from src.engine.context.budget import ContextBudget
from src.engine.context.strategy.constants import (
    DEFAULT_MAX_TOOL_RESULT_CHARS,
    DEFAULT_TOOL_RESULT_HEAD_CHARS,
    PINNED_TOOLS,
)
from src.engine.context.strategy.rewriting import drop_thinking, fit_to_budget, truncate_old_tool_results
from src.engine.context.strategy.turn_summary import summarize_turn
from src.engine.context.strategy.turns import has_pinned_result, split_turns
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_messages_tokens
from src.engine.context.working_set import DEFAULT_WORKING_SET_LIMIT, render_working_set, touched_files


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
            working, dropped_thinking = drop_thinking(working)

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
            if has_pinned_result(turn, self.pinned_tools):
                # Instrucciones cargadas: se conservan enteras y en su lugar.
                flush()
                result.extend(turn)
                continue
            text = summarize_turn(turn, max_chars=self.max_summary_chars)
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
                turn, count = truncate_old_tool_results(
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
            result, dropped = fit_keeping_summary(result, available, pinned_tools=self.pinned_tools)

        return ViewResult(
            messages=result,
            estimated_tokens=estimate_messages_tokens(result),
            dropped_messages=dropped,
            dropped_thinking=dropped_thinking,
            truncated_results=truncated,
            compacted_turns=compacted,
        )


def fit_keeping_summary(
    messages: list[ModelMessage],
    available: int,
    *,
    pinned_tools: frozenset[str] = frozenset(),
) -> tuple[list[ModelMessage], int]:
    """Como `fit_to_budget`, pero nunca descarta el resumen de compactación."""
    head: list[ModelMessage] = []
    rest = list(messages)
    if rest and isinstance(rest[0], ModelRequest) and any(isinstance(part, CompactionPart) for part in rest[0].parts):
        head = [rest.pop(0)]
    return fit_to_budget(rest, available, pinned_tools=pinned_tools, keep_head=head)
