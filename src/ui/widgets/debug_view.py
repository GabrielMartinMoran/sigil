"""Debug view: the agent side of the conversation.

It replaces the chat (`alt+d`) to answer a single question: **what does the
agent see?** The chat shows what the user read; this view shows what
travels in the request: resolved instructions, tool schemas, the history
projection after the context strategy and the prompt cache usage.

Everything painted comes from `DebugReport` (engine), so the widget
computes nothing: it only formats.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from src.engine.context.meter.debug import DebugMessage, DebugReport
from src.ui.widgets.reading import ReadOnlyScroll

_ROLE_STYLE = {'request': 'cyan', 'response': 'green', 'other': 'dim'}
_KIND_STYLE = {
    'user': 'bold white',
    'tool_return': 'green',
    'tool_call': 'cyan',
    'thinking': 'dim italic',
    'text': '',
    'system': 'magenta',
    'compaction': 'yellow',
    'retry': 'red',
}


def _n(value: float | None) -> str:
    if value is None:
        return '?'
    if isinstance(value, float):
        return f'{value:.0f}'
    return f'{value:,}'


def _pct(value: float | None) -> str:
    return '?' if value is None else f'{value:.0f}%'


def _hit_style(ratio: float) -> str:
    if ratio >= 0.7:
        return 'green'
    if ratio >= 0.3:
        return 'yellow'
    return 'red'


def build_debug_header(report: DebugReport) -> Text:
    """Fixed header: what has to be seen at a glance."""
    text = Text()
    text.append(' DEBUG ', style='bold black on yellow')
    text.append('  agent side of the conversation', style='bold')
    text.append('   alt+d to close\n', style='dim')

    text.append('  model ', style='dim')
    text.append(report.model or '?', style='bold')
    text.append('  effort ', style='dim')
    text.append(report.effort or '?', style='magenta')
    text.append('  window ', style='dim')
    text.append(_n(report.window), style='bold')
    text.append('\n')

    text.append('  log ', style='dim')
    text.append(f'{_n(report.log_tokens)} tok', style='bold')
    text.append(' → agent view ', style='dim')
    text.append(f'{_n(report.view_tokens)} tok', style='bold green')
    if report.dropped_tokens:
        text.append('  (', style='dim')
        text.append(f'−{_n(report.dropped_tokens)} tok', style='yellow')
        text.append(f', {_pct(report.reduction_percent)}', style='dim')
        text.append(')\n', style='dim')
    else:
        text.append('  (nothing dropped)\n', style='dim')

    cache = report.cache
    text.append('  cache ', style='dim')
    if not cache.has_data:
        text.append('no usage reported yet', style='dim')
        return text
    ratio = cache.hit_ratio
    text.append(f'{_pct(ratio * 100)} hit', style=f'bold {_hit_style(ratio)}')
    text.append('  cached ', style='dim')
    text.append(_n(cache.cached), style='green')
    text.append('  uncached ', style='dim')
    text.append(_n(cache.uncached), style='red')
    text.append('  writes ', style='dim')
    text.append(_n(cache.cache_write), style='yellow')
    if cache.cost is not None:
        text.append('  cost ', style='dim')
        text.append(f'${cache.cost:.4f}', style='bold')
    return text


def _fixed_cost_table(report: DebugReport) -> RenderableType:
    if not report.request.captured:
        # With no request in this process there is no measurement: showing "0 blocks · 0 tools"
        # would suggest the agent runs with no instructions and no tools.
        return Text(
            'Fixed cost per request\n'
            '  No request captured in this process yet (loaded session?).\n'
            '  Send a prompt once and this section fills in with the real blocks and schemas.',
            style='dim',
        )

    table = Table(title='Fixed cost per request', title_justify='left', box=None, pad_edge=False, expand=False)
    table.add_column('block', style='bold')
    table.add_column('tokens', justify='right')
    table.add_column('note', style='dim')

    for block in report.request.instructions:
        note = 'dynamic · recomputed per request' if block.dynamic else 'static'
        style = 'yellow' if block.dynamic else ''
        table.add_row(f'instruction {block.name}', _n(block.tokens), note, style=style)
    for tool in report.request.tools:
        note = 'deferred schema' if tool.deferred else ''
        table.add_row(f'tool {tool.name}', _n(tool.tokens), note)

    total = report.request.instructions_tokens + report.request.tools_tokens
    table.add_row('total', _n(total), f'{len(report.request.instructions)} blocks · {len(report.request.tools)} tools')
    return table


def _cache_table(report: DebugReport) -> RenderableType:
    cache = report.cache
    if not cache.has_data:
        return Text('No usage reported yet.', style='dim')

    table = Table(title='Prompt cache per request', title_justify='left', box=None, pad_edge=False, expand=False)
    table.add_column('#', justify='right', style='dim')
    table.add_column('input', justify='right')
    table.add_column('read', justify='right', style='green')
    table.add_column('written', justify='right', style='yellow')
    table.add_column('uncached', justify='right', style='red')
    table.add_column('hit', justify='right')
    table.add_column('output', justify='right', style='dim')

    for row in cache.rows:
        table.add_row(
            str(row.index),
            _n(row.input_tokens),
            _n(row.cache_read),
            _n(row.cache_write),
            _n(row.uncached),
            Text(_pct(row.hit_ratio * 100), style=_hit_style(row.hit_ratio)),
            _n(row.output_tokens),
        )

    total_style = _hit_style(cache.hit_ratio)
    table.add_row(
        'all',
        _n(cache.input_tokens),
        _n(cache.cache_read),
        _n(cache.cache_write),
        _n(cache.uncached),
        Text(_pct(cache.hit_ratio * 100), style=f'bold {total_style}'),
        _n(cache.output_tokens),
    )
    return table


def _message_renderable(row: DebugMessage, *, dim: bool = False) -> RenderableType:
    text = Text()
    role_style = _ROLE_STYLE.get(row.role, 'dim')
    text.append(f'[{row.index}] ', style='dim')
    text.append(f'{row.role:<8}', style=role_style)
    text.append(f'{_n(row.tokens):>8} tok  ', style='bold' if not dim else 'dim')
    text.append(row.kinds, style='dim')
    if row.instructions:
        text.append('   +instructions: ', style='dim')
        text.append(', '.join(row.instructions), style='magenta')
    text.append('\n')

    for part in row.parts:
        style = _KIND_STYLE.get(part.kind, '')
        if dim:
            style = f'{style} dim'.strip()
        text.append(f'      {part.kind}', style=style or 'dim')
        text.append(f' {_n(part.tokens)} tok / {part.chars:,} chars\n', style='dim')
        if part.preview.strip():
            preview = '        ' + part.preview.replace('\n', '\n        ') + '\n'
            text.append(preview, style=f'{style} dim'.strip() if style else 'dim')
        if part.clipped:
            text.append(f'        … {part.clipped_chars:,} more chars not shown\n', style='italic dim')
    return text


def _messages_section(title: str, rows: tuple[DebugMessage, ...], *, empty: str, dim: bool = False) -> Group:
    blocks: list[RenderableType] = [Text(title, style='bold underline')]
    if not rows:
        blocks.append(Text(empty, style='dim'))
        return Group(*blocks)
    for row in rows:
        blocks.append(_message_renderable(row, dim=dim))
    return Group(*blocks)


def build_debug_body(report: DebugReport) -> RenderableType:
    """Scrollable body: fixed cost, cache, agent view and full log."""
    sections: list[RenderableType] = [
        _fixed_cost_table(report),
        Text(''),
        _cache_table(report),
    ]

    if report.strategies_note:
        note = Text()
        note.append('Applied context strategy\n', style='bold underline')
        note.append(f'  {report.strategies_note}\n', style='dim')
        sections.extend([Text(''), note])

    sections.extend(
        [
            Text(''),
            _messages_section(
                'Agent view — what the model receives',
                report.view,
                empty='No projected view yet (no request has run in this session).',
            ),
            Text(''),
            _messages_section(
                'Session log — what is persisted and shown in the chat',
                report.log,
                empty='Empty session.',
                dim=True,
            ),
        ]
    )
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
        """Repaints the view with a new report."""
        self._report = report
        self._header.update(build_debug_header(report))
        self._body.update(build_debug_body(report))
