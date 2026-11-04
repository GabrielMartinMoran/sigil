"""Texto de `/context`, listo para el chat."""

from __future__ import annotations

from src.engine.context.meter.report import ContextReport


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
