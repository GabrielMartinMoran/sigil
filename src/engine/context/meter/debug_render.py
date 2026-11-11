"""Content-free plain-text diagnostic for `/debug --text`."""

from __future__ import annotations

from collections.abc import Sequence

from src.engine.context.compaction.events import CompactionEvent
from src.engine.context.meter.cache import format_call_cost, format_call_time
from src.engine.context.meter.debug import DebugMessage, DebugReport


def _n(value: float | None) -> str:
    return '?' if value is None else f'{value:,.0f}'


def _events(events: Sequence[CompactionEvent]) -> str:
    """The boundaries of a turn, inline: step and reason."""
    if not events:
        return '-'
    return ', '.join(f'step {event.step} {event.reason}' for event in events)


def _percent(ratio: float | None) -> str:
    return '?' if ratio is None else f'{ratio * 100:.0f}%'


def composition(rows: tuple[DebugMessage, ...]) -> dict[str, tuple[int, int]]:
    """Estimated token counts by message-part type, without their contents."""
    totals: dict[str, tuple[int, int]] = {}
    for row in rows:
        for part in row.parts:
            count, tokens = totals.get(part.kind, (0, 0))
            totals[part.kind] = count + 1, tokens + part.tokens
    return totals


def render_debug_report(report: DebugReport) -> str:
    """Format measurements and estimates without showing conversation text."""
    lines = [
        'Agent debug',
        f'  model: {report.model or "?"}  effort: {report.effort or "?"}',
        '',
        'Context (next request; estimated)',
        f'  window: {_n(report.window)} tokens',
        f'  last context (reported input + output): {_n(report.snapshot.measured)} tokens',
        f'  projected total: {_n(report.snapshot.projected)} tokens',
        f'  log: {_n(report.log_tokens)} tokens',
        f'  projected history: {_n(report.view_tokens) if report.has_view else "?"} tokens',
        f'  reduction: {_n(report.dropped_tokens) if report.has_view else "?"} tokens',
        '',
        'Fixed cost per request (estimated; last captured request)',
    ]
    if not report.request.captured:
        lines.append('  (no request captured in this process yet)')
    else:
        lines.append(f'  instructions: {_n(report.request.instructions_tokens)} tokens')
        lines.append(f'  tool schemas: {_n(report.request.tools_tokens)} tokens')
        lines.append(f'  native tools: {report.request.native_tools} (not included in schema estimate)')
        lines.append('  Instruction blocks')
        for block in report.request.instructions:
            lines.append(f'    {block.name}: {_n(block.tokens)} tokens ({"dynamic" if block.dynamic else "static"})')
        lines.append('  Tool schemas')
        for tool in report.request.tools:
            lines.append(f'    {tool.name}: {_n(tool.tokens)} tokens{" (deferred)" if tool.deferred else ""}')

    lines.extend(['', 'Last request · sections (estimated tokens)'])
    if not report.request.captured:
        lines.append('  No request captured in this process yet.')
    elif not report.request.history_captured:
        lines.append('  History not captured; see fixed sections above.')
    else:
        for part in report.request.sections:
            lines.append(f'  {part.label} | {part.kind} | {_n(part.tokens)}')
        if report.request.native_tools:
            lines.append('  Native tool schemas: size unknown')

    cache = report.cache
    lines.extend(['', 'Prompt cache (reported usage)', '  Reads are hits; writes are not. Zero may mean not reported.'])
    if not cache.has_data:
        lines.append('  (no usage reported yet)')
    else:
        lines.extend(
            [
                f'  measured requests: {cache.requests}; unmeasured: {cache.unmeasured}',
                f'  input: {_n(cache.input_tokens)}  read: {_n(cache.cache_read)}  written: {_n(cache.cache_write)}',
                f'  normal input: {_n(cache.uncached)}  read share: {_percent(cache.hit_ratio)}  output: {_n(cache.output_tokens)}',
                '  Per request: local response time | # | input | read | written | normal input | read share | output | cost',
                '  Cost: ~ is estimated; ? is unavailable.',
            ]
        )
        for row in reversed(cache.rows):
            lines.append(
                f'    {format_call_time(row.timestamp)} | {row.index} | {_n(row.input_tokens)} | '
                f'{_n(row.cache_read)} | {_n(row.cache_write)} | {_n(row.uncached)} | {_percent(row.hit_ratio)} | '
                f'{_n(row.output_tokens)} | {format_call_cost(row.cost, row.cost_estimated)}'
            )
        if not cache.valid:
            lines.append('  Inconsistent cache counters: derived values are unavailable.')
        if cache.cost is not None:
            lines.append(
                f'  known cost (includes {cache.estimated_cost_requests} estimates): ${cache.cost:.6f} '
                f'({cache.cost_requests} of {cache.requests + cache.unmeasured} responses)'
            )
    if cache.unmeasured and not cache.has_data:
        lines.append(f'  unmeasured: {cache.unmeasured} responses')

    lines.extend(
        [
            '',
            'Turns  (# | context used | cache read | read % | compaction events | measured | missing)',
        ]
    )
    for turn in report.turns:
        lines.append(
            (
                f'  {turn.index} | {_n(turn.context_used)} | {_n(turn.cache_read) if turn.measured else "?"} | '
                f'{_percent(turn.cache_ratio)} | {_events(turn.events)} | {turn.measured} | {turn.unmeasured}'
            ),
        )

    lines.extend(['', *maintenance_lines(report)])
    if report.strategies_note:
        lines.extend(['', 'Applied view', f'  {report.strategies_note}'])
    lines.extend(
        [
            '',
            'History composition (estimated tokens; excludes fixed instructions and tool schemas)',
            '  type | log count | log tokens | projected count | projected tokens',
        ]
    )
    log = composition(report.log)
    view = composition(report.view)
    for kind in sorted(log.keys() | view.keys()):
        log_count, log_tokens = log.get(kind, (0, 0))
        view_count, view_tokens = view.get(kind, (0, 0))
        projected = (
            f'{view_count} | {composition_tokens(report.view, kind, view_tokens)}' if report.has_view else '? | ?'
        )
        lines.append(f'  {kind} | {log_count} | {composition_tokens(report.log, kind, log_tokens)} | {projected}')
    return '\n'.join(lines)


def maintenance_lines(report: DebugReport) -> list[str]:
    """Operational diagnostics shared by text and TUI; never render payloads."""
    lines = ['Maintenance (next request projection; historical records distinguished below)']
    view = report.view_result
    if view is None or view.diagnostics is None:
        return [*lines, '  No maintenance projection available.']
    data = view.diagnostics
    exceptions = data.get('exceptions')
    reasons = ', '.join(str(reason) for reason in exceptions) if isinstance(exceptions, (list, tuple)) else 'none'
    lines.extend(
        [
            (
                f'  reasoning blocks kept: {data["reasoning_blocks"]}; '
                f'size {data["reasoning_size"]} ~{data["reasoning_known_tokens"]} tokens; '
                f'unknown signed blocks {data["reasoning_unknown_blocks"]}'
            ),
            (
                f'  compacted calls: {data["compacted_calls"]}; '
                f'dropped reasoning parts: {data["dropped_parts"]}; '
                f'summary: {data["summary_chars"]} chars'
            ),
            (f'  thresholds: trigger {data["trigger_percent"]}%; target {data["target_percent"]}%'),
            (
                f'  estimated reductions: arguments ~{data["input_reduction_tokens"]} tokens; '
                f'results ~{data["output_reduction_tokens"]} tokens'
            ),
            f'  exceptions: {reasons or "none"}',
            f'  over budget: {view.budget_exceeded}',
            f'  last checkpoint failure: {view.summary_error or "none"}',
            (
                f'  safety reserve: {data.get("safety_reserve", 0)} tokens; '
                f'input source: {data.get("input_estimate_source", "local")}'
            ),
        ]
    )
    recorded = view.plan.recorded_events if view.plan is not None else 0
    for index, event in enumerate(view.events[-2:], start=max(0, len(view.events) - 2)):
        origin = 'recorded' if index < recorded else 'projected/reconstructed'
        lines.append(
            (
                f'  {origin}: step {event.step}; {event.reason}; policy v{event.policy_version}; '
                f'window {event.window if event.window is not None else "?"}; '
                f'unmet {", ".join(event.unmet) or "none"}; counters {event.detail}'
            ),
        )
    if report.request.skills:
        skills = report.request.skills
        lines.append(
            (
                f'  skills (last captured request; estimated): instructions ~{skills["instructions_tokens"]} tokens; '
                f'resources ~{skills["resources_tokens"]} tokens; resources {skills["resources"]}; '
                f'partial {skills["partial"]}; binary {skills["binary"]}; unavailable {skills["unavailable"]}; '
                f'unavailable skills {skills["unavailable_skills"]}'
            ),
        )
    return lines


def composition_tokens(rows: tuple[DebugMessage, ...], kind: str, known: int) -> str:
    unknown = sum(part.unknown_tokens for row in rows for part in row.parts if part.kind == kind)
    return f'{known:,} + ? ({unknown} unknown)' if unknown else _n(known)
