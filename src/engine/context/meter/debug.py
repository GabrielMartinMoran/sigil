"""Modelo de la vista de debug: qué tiene el agente en contexto y cuánto cuesta.

La vista de debug muestra **el lado del agente** de la conversación. Dos listas:

- **log**: los mensajes de la sesión, tal como se persisten y se ven en el chat.
- **vista**: los mensajes que el agente recibe en el próximo request, después de
  aplicar la estrategia de contexto (razonamiento dropeado, tool results viejos
  truncados, turnos viejos colapsados o descartados).

La diferencia entre las dos es exactamente lo que la UI no muestra y lo que hace
falta para entender por qué el agente se olvidó de algo. Además se reporta el
costo fijo del request (instrucciones y schemas de tools) y el uso del prompt
cache, que es lo que dice si el prefijo del prompt es estable.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.messages import (
    CompactionPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.context.meter.cache import CacheSummary, cache_summary
from src.engine.context.meter.request import RequestSnapshot
from src.engine.context.meter.snapshot import ContextSnapshot
from src.engine.context.strategy.view_result import ViewResult
from src.engine.context.tokens import estimate_message_tokens, estimate_tokens, part_text, tool_return_text

DEFAULT_PART_CHARS = 700
"""Cuántos caracteres de cada parte se muestran. El costo real va aparte, en tokens."""

DEFAULT_PART_LINES = 6
"""Cuántas líneas del preview se muestran. Un tool result es 1% señal y 99% líneas."""


@dataclass(frozen=True, slots=True)
class DebugPart:
    """Una parte de un mensaje, con su costo y un preview acotado."""

    kind: str
    label: str
    tokens: int
    chars: int
    preview: str
    clipped_chars: int = 0
    """Caracteres que no se muestran por el recorte del preview."""

    @property
    def clipped(self) -> bool:
        return self.clipped_chars > 0


@dataclass(frozen=True, slots=True)
class DebugMessage:
    """Un mensaje con sus partes, medido en tokens."""

    index: int
    role: str
    """``request`` (lo que entra al modelo) o ``response`` (lo que el modelo produjo)."""
    tokens: int
    parts: tuple[DebugPart, ...] = ()
    instructions: tuple[str, ...] = ()
    """Nombres de los bloques de instrucciones que viajan con este request."""

    @property
    def kinds(self) -> str:
        """Resumen de una línea: ``user · tool_return ×3``."""
        if not self.parts:
            return '(empty)'
        counts: dict[str, int] = {}
        for part in self.parts:
            counts[part.kind] = counts.get(part.kind, 0) + 1
        return ' · '.join(f'{kind} ×{count}' if count > 1 else kind for kind, count in counts.items())


@dataclass(frozen=True, slots=True)
class DebugReport:
    """Todo lo que pinta la vista de debug."""

    snapshot: ContextSnapshot
    request: RequestSnapshot
    cache: CacheSummary
    log: tuple[DebugMessage, ...] = ()
    view: tuple[DebugMessage, ...] = ()
    view_result: ViewResult | None = None
    log_tokens: int = 0
    view_tokens: int = 0
    window: int | None = None
    effort: str | None = None
    model: str | None = None
    strategies_note: str = ''

    @property
    def dropped_tokens(self) -> int:
        """Tokens del log que el agente no va a ver en el próximo request."""
        return max(0, self.log_tokens - self.view_tokens)

    @property
    def reduction_percent(self) -> float | None:
        if not self.log_tokens:
            return None
        return self.dropped_tokens / self.log_tokens * 100.0

    @property
    def has_view(self) -> bool:
        return self.view_result is not None


def _clip(text: str, limit: int, max_lines: int) -> tuple[str, int]:
    """Recorta por caracteres **y** por líneas: un log de 700 chars son 100 líneas de ruido."""
    clipped = text[:limit]
    lines = clipped.splitlines()
    if len(lines) > max_lines:
        clipped = '\n'.join(lines[:max_lines])
    return clipped, len(text) - len(clipped)


def _part(kind: str, label: str, text: str, limit: int, max_lines: int) -> DebugPart:
    preview, clipped = _clip(text, limit, max_lines)
    return DebugPart(
        kind=kind,
        label=label,
        tokens=estimate_tokens(text),
        chars=len(text),
        preview=preview,
        clipped_chars=clipped,
    )


def _request_part(part: object, limit: int, max_lines: int) -> DebugPart | None:
    if isinstance(part, UserPromptPart):
        content = part.content if isinstance(part.content, str) else ' '.join(str(item) for item in part.content)
        return _part('user', 'user prompt', content, limit, max_lines)
    if isinstance(part, ToolReturnPart):
        text = tool_return_text(part)
        outcome = 'ok' if getattr(part, 'outcome', 'success') == 'success' else getattr(part, 'outcome', 'error')
        return _part('tool_return', f'{part.tool_name} → {outcome}', text, limit, max_lines)
    if isinstance(part, RetryPromptPart):
        return _part('retry', f'{part.tool_name or "tool"} retry', part_text(part), limit, max_lines)
    if isinstance(part, SystemPromptPart):
        return _part('system', 'system prompt', part.content or '', limit, max_lines)
    if isinstance(part, CompactionPart):
        return _part('compaction', 'compaction summary', part.content or '', limit, max_lines)
    return None


def _response_part(part: object, limit: int, max_lines: int) -> DebugPart | None:
    if isinstance(part, TextPart):
        return _part('text', 'assistant text', part.content or '', limit, max_lines)
    if isinstance(part, ThinkingPart):
        return _part('thinking', 'thinking', part.content or '', limit, max_lines)
    if isinstance(part, ToolCallPart):
        args = part.args_as_json_str() if hasattr(part, 'args_as_json_str') else str(part.args)
        return _part('tool_call', f'call {part.tool_name}', args, limit, max_lines)
    return None


def _instructions_of(message: ModelMessage) -> tuple[str, ...]:
    """Nombres de los bloques de instrucciones que viajan en el request."""
    instructions = getattr(message, 'instructions', None)
    if not instructions:
        return ()
    if isinstance(instructions, str):
        return ('(inline)',)
    names = []
    for part in instructions:
        names.append(getattr(part, 'name', None) or '(base)')
    return tuple(names)


def debug_messages(
    messages: list[ModelMessage] | tuple[ModelMessage, ...],
    *,
    part_chars: int = DEFAULT_PART_CHARS,
    part_lines: int = DEFAULT_PART_LINES,
) -> list[DebugMessage]:
    """Convierte mensajes de pydantic-ai en filas medibles para la vista."""
    rows: list[DebugMessage] = []
    for index, message in enumerate(messages):
        parts: list[DebugPart] = []
        if isinstance(message, ModelRequest):
            role = 'request'
            for part in message.parts:
                row = _request_part(part, part_chars, part_lines)
                if row is not None:
                    parts.append(row)
            instructions = _instructions_of(message)
        elif isinstance(message, ModelResponse):
            role = 'response'
            for part in message.parts:
                row = _response_part(part, part_chars, part_lines)
                if row is not None:
                    parts.append(row)
            instructions = ()
        else:
            role = 'other'
            instructions = ()
        rows.append(
            DebugMessage(
                index=index,
                role=role,
                tokens=estimate_message_tokens(message),
                parts=tuple(parts),
                instructions=instructions,
            )
        )
    return rows


def build_debug_report(
    messages: list[ModelMessage],
    *,
    snapshot: ContextSnapshot,
    request: RequestSnapshot,
    view_result: ViewResult | None = None,
    effort: str | None = None,
    model: str | None = None,
    part_chars: int = DEFAULT_PART_CHARS,
    part_lines: int = DEFAULT_PART_LINES,
) -> DebugReport:
    """Arma el reporte de debug a partir del log y de la vista proyectada."""
    log = debug_messages(messages, part_chars=part_chars, part_lines=part_lines)
    log_tokens = sum(row.tokens for row in log)

    if view_result is not None:
        view = debug_messages(view_result.messages, part_chars=part_chars, part_lines=part_lines)
        view_tokens = sum(row.tokens for row in view)
        strategies_note = (
            f'dropped thinking: {view_result.dropped_thinking} · '
            f'truncated results: {view_result.truncated_results} · '
            f'compacted turns: {view_result.compacted_turns} · '
            f'dropped messages: {view_result.dropped_messages}'
        )
    else:
        view = []
        view_tokens = log_tokens
        strategies_note = ''

    return DebugReport(
        snapshot=snapshot,
        request=request,
        cache=cache_summary(messages),
        log=tuple(log),
        view=tuple(view),
        view_result=view_result,
        log_tokens=log_tokens,
        view_tokens=view_tokens,
        window=snapshot.window,
        effort=effort,
        model=model,
        strategies_note=strategies_note,
    )
