"""Medición del contexto usado: medido, proyectado y desglose.

El medido es ground truth pero describe el request anterior. El proyectado es lo
que importa para saber si el próximo entra en la ventana. La barra de estado
muestra el proyectado; `/context` muestra los dos y el desglose.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart

from src.engine.context.budget import DEFAULT_OUTPUT_RESERVE, ContextBudget
from src.engine.context.strategy import ViewResult, split_turns
from src.engine.context.tokens import (
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
    measured_tokens,
    part_text,
    projected_tokens,
)


def percent(used: int | None, window: int | None) -> float | None:
    """Porcentaje de ventana usada, o ``None`` si falta información."""
    if not window or used is None:
        return None
    return max(0.0, used / window * 100.0)


@dataclass(frozen=True, slots=True)
class TurnUsage:
    """Costo de un turno de la conversación."""

    index: int
    label: str
    tokens: int
    messages: int


@dataclass(frozen=True, slots=True)
class ToolResultUsage:
    """Costo de un tool result, para encontrar los que se comen la ventana."""

    tool_name: str
    tokens: int
    chars: int


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Los dos números de contexto y el presupuesto vigente."""

    measured: int | None
    projected: int | None
    window: int | None
    budget: ContextBudget

    @property
    def measured_percent(self) -> float | None:
        return percent(self.measured, self.window)

    @property
    def projected_percent(self) -> float | None:
        return percent(self.projected, self.window)


@dataclass(frozen=True, slots=True)
class ContextReport:
    """Desglose completo para `/context`."""

    snapshot: ContextSnapshot
    instructions: int
    tools: int
    history: int
    turns: list[TurnUsage] = field(default_factory=list)
    heaviest_tool_results: list[ToolResultUsage] = field(default_factory=list)
    view: ViewResult | None = None


class ContextMeter:
    """Mide el contexto de una sesión y arma el presupuesto."""

    def __init__(self, *, window: int | None = None, output_reserve: int = DEFAULT_OUTPUT_RESERVE) -> None:
        self._window = window if window and window > 0 else None
        self._output_reserve = output_reserve
        self._instructions = 0
        self._tools = 0

    @property
    def window(self) -> int | None:
        return self._window

    def set_window(self, window: int | None) -> None:
        if window and window > 0:
            self._window = window

    def set_overhead(self, *, instructions: int | None = None, tools: int | None = None) -> None:
        """Fija el costo de los bloques que se mandan siempre."""
        if instructions is not None:
            self._instructions = instructions
        if tools is not None:
            self._tools = tools

    def budget(self) -> ContextBudget:
        return ContextBudget(
            window=self._window,
            tools=self._tools,
            instructions=self._instructions,
            output_reserve=self._output_reserve,
        )

    def snapshot(self, messages: Sequence[ModelMessage]) -> ContextSnapshot:
        return ContextSnapshot(
            measured=measured_tokens(messages),
            projected=projected_tokens(messages),
            window=self._window,
            budget=self.budget(),
        )

    def report(self, messages: Sequence[ModelMessage], view: ViewResult | None = None) -> ContextReport:
        return ContextReport(
            snapshot=self.snapshot(messages),
            instructions=self._instructions,
            tools=self._tools,
            history=estimate_messages_tokens(messages),
            turns=_turn_usage(messages),
            heaviest_tool_results=_heaviest_tool_results(messages),
            view=view,
        )


def _turn_label(turn: Sequence[ModelMessage]) -> str:
    """Primer texto del usuario en el turno, recortado."""
    for message in turn:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            text = ' '.join(part_text(part).split())
            if text:
                return text if len(text) <= 48 else text[:47] + '…'
    return '(no prompt)'


def _turn_usage(messages: Sequence[ModelMessage]) -> list[TurnUsage]:
    usage = []
    for index, turn in enumerate(split_turns(messages)):
        usage.append(
            TurnUsage(
                index=index,
                label=_turn_label(turn),
                tokens=sum(estimate_message_tokens(message) for message in turn),
                messages=len(turn),
            )
        )
    return usage


def _heaviest_tool_results(messages: Sequence[ModelMessage], limit: int = 5) -> list[ToolResultUsage]:
    results: list[ToolResultUsage] = []
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            text = part_text(part)
            results.append(
                ToolResultUsage(
                    tool_name=part.tool_name or '?',
                    tokens=estimate_tokens(text),
                    chars=len(text),
                )
            )
    results.sort(key=lambda item: item.tokens, reverse=True)
    return results[:limit]


def _fmt_tokens(value: int | None) -> str:
    if value is None:
        return '?'
    return f'{value:,}'


def _fmt_percent(value: float | None) -> str:
    return '?' if value is None else f'{value:.0f}%'


def render_report(report: ContextReport) -> str:
    """Texto de `/context`, listo para el chat."""
    snapshot = report.snapshot
    budget = snapshot.budget
    lines = [
        'Context',
        f'  window           : {_fmt_tokens(snapshot.window)}',
        f'  measured         : {_fmt_tokens(snapshot.measured)} ({_fmt_percent(snapshot.measured_percent)})',
        f'  projected        : {_fmt_tokens(snapshot.projected)} ({_fmt_percent(snapshot.projected_percent)})',
        '',
        'Budget',
        f'  tools            : {_fmt_tokens(report.tools)}',
        f'  instructions     : {_fmt_tokens(report.instructions)}',
        f'  output reserve   : {_fmt_tokens(budget.output_reserve)}',
        f'  history          : {_fmt_tokens(report.history)}',
        f'  available        : {_fmt_tokens(budget.available_for_history)}',
    ]

    if report.view is not None and report.view.changed:
        view = report.view
        lines.extend(
            [
                '',
                'Applied view',
                f'  dropped turns        : {view.dropped_messages} messages',
                f'  dropped reasoning    : {view.dropped_thinking} parts',
                f'  truncated results    : {view.truncated_results}',
                f'  estimated tokens     : {_fmt_tokens(view.estimated_tokens)}',
            ]
        )

    if report.turns:
        lines.extend(['', 'Turns'])
        for turn in report.turns:
            lines.append(f'  [{turn.index}] {_fmt_tokens(turn.tokens):>8} tok  {turn.label}')

    if report.heaviest_tool_results:
        lines.extend(['', 'Heaviest tool results'])
        for item in report.heaviest_tool_results:
            lines.append(f'  {_fmt_tokens(item.tokens):>8} tok  {item.tool_name} ({item.chars:,} chars)')

    return '\n'.join(lines)
