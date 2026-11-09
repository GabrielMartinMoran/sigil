"""Content-free context and cache diagnostics shown in place of the chat."""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from src.engine.context.meter.debug import DebugReport
from src.ui.widgets.debug_styles import DYNAMIC_STYLE, PART_STYLES, cache_style, context_style
from src.ui.widgets.reading import ReadOnlyScroll


def _n(value: float | None) -> str:
    return '?' if value is None else f'{value:,.0f}'


def _pct(ratio: float | None) -> str:
    return '?' if ratio is None else f'{ratio * 100:.0f}%'


def build_debug_header(report: DebugReport) -> Text:
    """Fixed summary: context projection and observed cache reads."""
    text = Text()
    text.append(' DEBUG ', style='bold black on yellow')
    text.append('  Context and cache', style='bold')
    text.append('   alt+d to close\n', style='dim')
    text.append(f'  model {report.model or "?"}  effort {report.effort or "?"}  window {_n(report.window)}\n')
    projected = _n(report.view_tokens) if report.has_view else '?'
    text.append(f'  log ~{_n(report.log_tokens)} → projected history ~{projected} tokens\n')
    text.append('  last context ')
    text.append_text(_context_cell(report.snapshot.measured, report.window))
    text.append('  projected total ~')
    text.append_text(_context_cell(report.snapshot.projected, report.window))
    text.append('\n')
    cache = report.cache
    if cache.has_data:
        text.append(f'  cache read {_pct(cache.hit_ratio)}  ', style=cache_style(cache.hit_ratio))
        text.append(f'read {_n(cache.cache_read)}  written {_n(cache.cache_write)}  normal {_n(cache.uncached)}')
    else:
        text.append('  cache: no usage reported yet', style='dim')
    return text


def _context_cell(used: int | None, window: int | None) -> Text:
    ratio = used / window if used is not None and window and window > 0 else None
    return Text(f'{_n(used)} / {_n(window)} ({_pct(ratio)})', style=context_style(ratio))


def _last_request_table(report: DebugReport) -> RenderableType:
    """Logical sections of the last prepared request, not the next projection."""
    request = report.request
    if not request.captured:
        return Text('Last request · sections\n  No request captured in this process yet.', style='dim')
    table = Table(title='Last request · sections · estimated tokens', title_justify='left', box=None, pad_edge=False)
    table.add_column('Section')
    table.add_column('Type')
    table.add_column('Tokens', justify='right')
    for block in request.instructions:
        kind = 'dynamic' if block.dynamic else 'static'
        table.add_row(Text(block.name), kind, _n(block.tokens), style=PART_STYLES.get(kind))
    if request.tools:
        table.add_row('Tool schemas', 'schema', _n(request.tools_tokens), style=PART_STYLES['schema'])
    if request.native_tools:
        table.add_row(f'{request.native_tools} native tool schemas', 'schema', '?', style=PART_STYLES['schema'])
    for part in request.sections:
        table.add_row(Text(part.label), part.kind, _n(part.tokens), style=PART_STYLES.get(part.kind))
    total = request.instructions_tokens + request.tools_tokens + sum(part.tokens or 0 for part in request.sections)
    incomplete = (
        not request.history_captured or request.native_tools or any(part.tokens is None for part in request.sections)
    )
    table.add_row(
        'Known total' if incomplete else 'Total estimate', 'partial' if incomplete else '', _n(total), style='bold'
    )
    table.caption = (
        'Logical order, not provider wire order. Native schemas and encrypted reasoning sizes may be unknown.'
        if request.history_captured
        else 'History not captured; fixed sections only.'
    )
    return table


def _prompt_stack(report: DebugReport) -> RenderableType:
    """Show the captured request's logical blocks, without retaining their content."""
    request = report.request
    if not request.captured:
        return Text('Last request · prompt stack\n  No request captured in this process yet.', style='dim')

    blocks: list[tuple[str, str, int | None]] = []
    blocks.extend(
        (block.name, 'dynamic' if block.dynamic else 'static', block.tokens) for block in request.instructions
    )
    if request.tools:
        blocks.append(('Tool schemas', 'schema', request.tools_tokens))
    if request.native_tools:
        blocks.append((f'{request.native_tools} native tool schemas', 'schema', None))
    blocks.extend((part.label, part.kind, part.tokens) for part in request.sections)

    panels: list[RenderableType] = [Text('Last request · prompt stack · estimated tokens', style='bold')]
    for label, kind, tokens in blocks:
        style = DYNAMIC_STYLE if kind == 'dynamic' else PART_STYLES['schema'] if kind == 'schema' else 'white'
        panels.append(Panel(Text(f'{label}  ·  {_n(tokens)} tokens'), border_style=style, padding=(0, 1)))
    if not request.history_captured:
        panels.append(Text('History not captured; fixed sections only.', style='dim'))
    panels.append(Text('Logical order; provider wire order can differ. ? = unknown.', style='dim'))
    return Group(*panels)


def _fixed_cost_table(report: DebugReport) -> RenderableType:
    if not report.request.captured:
        return Text('Fixed cost per request (estimate)\n  No request captured in this process yet.', style='dim')
    table = Table(
        title='Fixed cost per request · last captured · estimated', title_justify='left', box=None, pad_edge=False
    )
    table.add_column('Component')
    table.add_column('Tokens', justify='right')
    table.add_column('Notes', style='dim')
    for block in report.request.instructions:
        table.add_row(
            f'instruction {block.name}',
            _n(block.tokens),
            'dynamic' if block.dynamic else 'static',
            style=DYNAMIC_STYLE if block.dynamic else None,
        )
    for tool in report.request.tools:
        table.add_row(f'tool {tool.name}', _n(tool.tokens), 'deferred' if tool.deferred else '')
    table.add_row(
        'total estimate',
        _n(report.request.instructions_tokens + report.request.tools_tokens),
        f'{report.request.native_tools} native tools not counted',
    )
    return table


def _cache_table(report: DebugReport) -> RenderableType:
    cache = report.cache
    if not cache.has_data:
        return Text(f'Prompt cache\n  No usage reported yet. Unmeasured responses: {cache.unmeasured}', style='dim')
    table = Table(title='Prompt cache · newest first · reported usage', title_justify='left', box=None, pad_edge=False)
    for name in ('#', 'Input', 'Read', 'Written', 'Normal', 'Read %', 'Output'):
        table.add_column(name, justify='right')
    for row in reversed(cache.rows):
        table.add_row(
            str(row.index),
            _context_cell(row.input_tokens, report.window),
            _n(row.cache_read),
            _n(row.cache_write),
            _n(row.uncached),
            Text(_pct(row.hit_ratio), style=cache_style(row.hit_ratio)),
            _n(row.output_tokens),
        )
    table.add_row(
        'all',
        _n(cache.input_tokens),
        _n(cache.cache_read),
        _n(cache.cache_write),
        _n(cache.uncached),
        Text(_pct(cache.hit_ratio), style=cache_style(cache.hit_ratio)),
        _n(cache.output_tokens),
        style='bold',
    )
    notes = [
        'Read = reused; written = stored for later, not a hit. Zero may mean not reported.',
        f'{cache.requests} measured, {cache.unmeasured} unmeasured responses.',
    ]
    if not cache.valid:
        notes.append('Inconsistent cache counters; derived values unavailable.')
    if cache.cost is not None:
        notes.append(
            f'Known cost: ${cache.cost:.6f} ({cache.estimated_cost_requests} estimates; '
            f'{cache.cost_requests} of {cache.requests + cache.unmeasured} responses).'
        )
    return Group(table, Text('\n'.join(notes), style='dim'))


def _turns_table(report: DebugReport) -> RenderableType:
    table = Table(title='Turns', title_justify='left', box=None, pad_edge=False)
    for name in ('#', 'Context used', 'Cache read', 'Read %', 'Compaction events', 'Measured', 'Missing'):
        table.add_column(name, justify='right')
    for turn in report.turns:
        table.add_row(
            str(turn.index),
            _context_cell(turn.context_used, report.window),
            _n(turn.cache_read) if turn.measured else '?',
            Text(_pct(turn.cache_ratio), style=cache_style(turn.cache_ratio)),
            ', '.join(f'step {event.step} {event.reason}' for event in turn.events) or '-',
            str(turn.measured),
            str(turn.unmeasured),
        )
    return table


def _composition_table(report: DebugReport) -> RenderableType:
    from src.engine.context.meter.debug_render import composition, composition_tokens

    table = Table(title='History composition · estimated tokens', title_justify='left', box=None, pad_edge=False)
    for name in ('Type', 'Log parts', 'Log tokens', 'View parts', 'View tokens'):
        table.add_column(name, justify='left' if name == 'Type' else 'right')
    log, view = composition(report.log), composition(report.view)
    for kind in sorted(log.keys() | view.keys()):
        log_count, log_tokens = log.get(kind, (0, 0))
        view_count, view_tokens = view.get(kind, (0, 0))
        table.add_row(
            kind,
            _n(log_count),
            composition_tokens(report.log, kind, log_tokens),
            _n(view_count) if report.has_view else '?',
            composition_tokens(report.view, kind, view_tokens) if report.has_view else '?',
            style=PART_STYLES.get(kind),
        )
    return table


def _turn_timeline(report: DebugReport) -> Text:
    turn = next((item for item in reversed(report.turns) if item.steps), None)
    if turn is None:
        return Text('Turn timeline\n  No completed steps yet.', style='dim')
    text = Text(f'Turn {turn.index} timeline · newest first\n', style='bold')
    step_width = len(str(len(turn.steps)))
    context_width = max(
        len(_context_cell(row.input_tokens if row else None, report.window).plain) for row in turn.steps
    )
    cache_width = max(len(_pct(row.hit_ratio if row else None)) for row in turn.steps)
    for index in range(len(turn.steps), 0, -1):
        row = turn.steps[index - 1]
        marker = '└' if index == 1 else '┌' if index == len(turn.steps) else '├'
        text.append(f'  {marker} Step {index:<{step_width}}  ', style='bold')
        used = row.input_tokens if row else None
        text.append('context ')
        context = _context_cell(used, report.window)
        text.append_text(context)
        text.append(' ' * (context_width - len(context.plain)))
        ratio = row.hit_ratio if row else None
        text.append('  cache ')
        cache = _pct(ratio)
        text.append(cache.ljust(cache_width), style=cache_style(ratio))
        events = turn.step_events[index - 1] if turn.step_events else ()
        for event in events:
            text.append(f'  ◆ compaction: {event.reason}', style='bold yellow')
        text.append('\n')
    return text


def build_debug_body(report: DebugReport) -> RenderableType:
    """Scrollable, content-free tables for cache, context, and request overhead."""
    sections: list[RenderableType] = [
        _turn_timeline(report),
        Text(''),
        _cache_table(report),
        Text(''),
        _prompt_stack(report),
        Text(''),
        _turns_table(report),
        Text(''),
        _composition_table(report),
    ]
    if report.strategies_note:
        sections.extend([Text(''), Text(f'Applied context strategy\n  {report.strategies_note}', style='dim')])
    from src.engine.context.meter.debug_render import maintenance_lines

    sections.extend([Text(''), Text('\n'.join(maintenance_lines(report))), Text(''), _fixed_cost_table(report)])
    return Group(*sections)


class AgentDebugView(Vertical):
    """Chat replacement: fixed header and scrollable body with the diagnostic.

    The header stays fixed because the summary (log vs view, cache hit) is what
    you look at while the turn runs; the body starts at the top and is scrolled
    by hand, without anchoring to the bottom: here the bottom is not the newest part.
    """

    DEFAULT_CSS = """
    AgentDebugView {
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._report: DebugReport | None = None
        self._header = Static(Text(''), id='debug-header')
        self._body = Static(Text(''), id='debug-body')

    def compose(self) -> ComposeResult:
        yield self._header
        with ReadOnlyScroll(id='debug-scroll'):
            yield self._body

    def on_mount(self) -> None:
        self.query_one('#debug-scroll', ReadOnlyScroll).scroll_home(animate=False)

    @property
    def last_report(self) -> DebugReport | None:
        """Last painted report. Tests use it to inspect without parsing the screen."""
        return self._report

    def update_report(self, report: DebugReport) -> None:
        """Repaints context and cache diagnostics."""
        self._report = report
        self._header.update(build_debug_header(report))
        self._body.update(build_debug_body(report))
