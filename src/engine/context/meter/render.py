"""Text of `/context`, ready for the chat."""

from __future__ import annotations

from src.engine.context.meter.cost import SessionCost, format_usd
from src.engine.context.meter.report import ContextReport


def _fmt_tokens(value: int | None) -> str:
    if value is None:
        return '?'
    return f'{value:,}'


def _fmt_percent(value: float | None) -> str:
    return '?' if value is None else f'{value:.0f}%'


def _cost_lines(cost: SessionCost) -> list[str]:
    """The bill of the session, with the provenance the panel only hints at.

    The panel says `~$0.04+`; here there is room for what the marks mean: how many
    requests the provider priced itself, how many came from a rate table, and how
    many could not be priced at all.
    """
    total = format_usd(cost.total)
    if cost.approximate:
        total = f'{total} (at list price)'
    lines = ['', 'Cost', f'  total            : {total}']

    buckets = []
    if cost.reported:
        buckets.append(f'{cost.reported} reported by the provider')
    if cost.estimated:
        buckets.append(f'{cost.estimated} at list price')
    if cost.unpriced:
        buckets.append(f'{cost.unpriced} unpriced')
    if cost.unmeasured:
        buckets.append(f'{cost.unmeasured} without usage')
    if buckets:
        lines.append(f'  requests         : {cost.requests} ({", ".join(buckets)})')
    if cost.partial:
        lines.append('  floor            : the total leaves out what could not be priced')
    return lines


def render_report(report: ContextReport) -> str:
    """Text of `/context`, ready for the chat."""
    snapshot = report.snapshot
    budget = snapshot.budget
    lines = [
        'Context',
        f'  window           : {_fmt_tokens(snapshot.window)}',
        f'  measured         : {_fmt_tokens(snapshot.measured)} ({_fmt_percent(snapshot.measured_percent)})',
        f'  projected        : {_fmt_tokens(snapshot.projected)} ({_fmt_percent(snapshot.projected_percent)})',
    ]
    lines.extend(_cost_lines(report.cost))
    lines.extend(
        [
            '',
            'Budget',
            f'  tools            : {_fmt_tokens(report.tools)}',
            f'  instructions     : {_fmt_tokens(report.instructions)}',
            f'  output reserve   : {_fmt_tokens(budget.output_reserve)}',
            f'  history          : {_fmt_tokens(report.history)}',
            f'  available        : {_fmt_tokens(budget.available_for_history)}',
        ]
    )
    if not report.tools and not report.instructions:
        # A session written before the measurement was persisted, or one that has not
        # made a request in this process yet. Saying so is better than passing the zero
        # off as a measurement: the room it leaves is not the room there is.
        lines.append('  note             : the fixed blocks are not measured in this session yet')

    if report.view is not None and report.view.changed:
        view = report.view
        lines.extend(
            [
                '',
                'Applied view',
                f'  dropped reasoning    : {view.dropped_thinking} parts',
                f'  compacted parts      : {view.compacted_parts}',
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
