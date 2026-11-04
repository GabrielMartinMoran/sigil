"""Vista de debug: el lado del agente de la conversación.

Reemplaza al chat (`alt+d`) para responder una sola pregunta: **¿qué ve el
agente?** El chat muestra lo que el usuario leyó; esta vista muestra lo que
viaja en el request: instrucciones resueltas, schemas de tools, la proyección
del historial después de la estrategia de contexto y el uso del prompt cache.

Todo lo que se pinta sale de `DebugReport` (engine), así que el widget no
calcula nada: sólo formatea.
"""

from __future__ import annotations

from typing import Any

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

from src.engine.context.meter.debug import DebugMessage, DebugReport

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
    """Cabecera fija: lo que hay que ver de un vistazo."""
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
        # Sin request en este proceso no hay medición: mostrar "0 blocks · 0 tools"
        # haría creer que el agente va sin instrucciones ni tools.
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
    """Cuerpo scrolleable: costo fijo, caché, vista del agente y log completo."""
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
    """Reemplazo del chat: cabecera fija y cuerpo scrolleable con el diagnóstico.

    La cabecera queda fija porque el resumen (log vs vista, hit de caché) es lo
    que se mira mientras el turno corre; el cuerpo arranca arriba y se scrollea
    a mano, sin anclaje al final: acá el final no es lo más nuevo.
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
        with VerticalScroll(id='debug-scroll'):
            yield self._body

    def on_mount(self) -> None:
        self.query_one('#debug-scroll', VerticalScroll).scroll_home(animate=False)

    @property
    def last_report(self) -> DebugReport | None:
        """Último reporte pintado. Lo usan los tests para inspeccionar sin parsear la pantalla."""
        return self._report

    def update_report(self, report: DebugReport) -> None:
        """Repinta la vista con un reporte nuevo."""
        self._report = report
        self._header.update(build_debug_header(report))
        self._body.update(build_debug_body(report))
