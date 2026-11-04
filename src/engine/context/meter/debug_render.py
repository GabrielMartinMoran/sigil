"""Render de la vista de debug a texto plano (`/debug --text` y tests)."""

from __future__ import annotations

from src.engine.context.meter.debug import DebugMessage, DebugReport


def _n(value: float | None) -> str:
    if value is None:
        return '?'
    if isinstance(value, float):
        return f'{value:.0f}'
    return f'{value:,}'


def _percent(value: float | None) -> str:
    return '?' if value is None else f'{value:.0f}%'


def _message_block(row: DebugMessage, *, indent: str = '') -> list[str]:
    head = f'{indent}[{row.index}] {row.role:<8} {_n(row.tokens):>8} tok  {row.kinds}'
    lines = [head]
    if row.instructions:
        lines.append(f'{indent}     instructions: {", ".join(row.instructions)}')
    for part in row.parts:
        clip = f' (+{part.clipped_chars:,} chars hidden)' if part.clipped else ''
        lines.append(f'{indent}     - {part.kind}/{part.label}: {_n(part.tokens)} tok, {part.chars:,} chars{clip}')
        if part.preview.strip():
            for preview_line in part.preview.splitlines():
                lines.append(f'{indent}       {preview_line}')
    return lines


def _section(title: str, rows: tuple[DebugMessage, ...], *, empty: str) -> list[str]:
    lines = ['', f'{title} ({len(rows)} messages)']
    if not rows:
        lines.append(f'  {empty}')
        return lines
    for row in rows:
        lines.extend(_message_block(row))
    return lines


def render_debug_report(report: DebugReport) -> str:
    """Texto plano del reporte, para tests y para `/debug --text`."""
    snapshot = report.snapshot
    lines = [
        'Agent debug',
        f'  model            : {report.model or "?"}',
        f'  effort           : {report.effort or "?"}',
        '',
        'Context (next request)',
        f'  window           : {_n(snapshot.window)}',
        f'  log              : {_n(report.log_tokens)} tok  (persisted history)',
        f'  agent view       : {_n(report.view_tokens)} tok  (what the model receives)',
        f'  not sent         : {_n(report.dropped_tokens)} tok ({_percent(report.reduction_percent)})',
        '',
        'Fixed cost per request',
    ]
    if not report.request.captured:
        # Sin request capturado, los ceros no son una medición.
        lines.append('  (no request captured in this process yet)')
    else:
        lines.extend(
            [
                (
                    f'  instructions     : {_n(report.request.instructions_tokens)} tok '
                    f'({len(report.request.instructions)} blocks)'
                ),
                f'  tool schemas     : {_n(report.request.tools_tokens)} tok ({len(report.request.tools)} tools)',
                f'  native tools     : {report.request.native_tools}',
            ]
        )

    if report.request.instructions:
        lines.append('')
        lines.append('Instruction blocks')
        for block in report.request.instructions:
            tag = 'dynamic' if block.dynamic else 'static'
            lines.append(f'  {_n(block.tokens):>8} tok  [{tag}] {block.name}')

    if report.request.tools:
        lines.append('')
        lines.append('Tool schemas')
        for tool in report.request.tools:
            tag = ' deferred' if tool.deferred else ''
            lines.append(f'  {_n(tool.tokens):>8} tok  {tool.name}{tag}')

    cache = report.cache
    lines.extend(['', 'Prompt cache'])
    if not cache.has_data:
        lines.append('  (no usage reported yet)')
        if cache.unmeasured:
            lines.append(f'  unmeasured requests: {cache.unmeasured}')
    else:
        lines.extend(
            [
                f'  requests         : {cache.requests}',
                f'  input            : {_n(cache.input_tokens)} tok',
                f'  cached           : {_n(cache.cached)} tok ({_percent(cache.hit_ratio * 100)})',
                f'    read           : {_n(cache.cache_read)} tok',
                f'    written        : {_n(cache.cache_write)} tok',
                f'  uncached         : {_n(cache.uncached)} tok ({_percent((1 - cache.hit_ratio) * 100)})',
                f'  output           : {_n(cache.output_tokens)} tok',
            ]
        )
        if cache.unmeasured:
            lines.append(f'  unmeasured       : {cache.unmeasured} requests')
        if cache.cost is not None:
            lines.append(f'  cost             : ${cache.cost:.4f}')
        lines.append('')
        lines.append('Per request')
        for row in cache.rows:
            lines.append(
                f'  #{row.index:<3} in {_n(row.input_tokens):>8}  cached {_n(row.cached):>8}  '
                f'uncached {_n(row.uncached):>8}  hit {_percent(row.hit_ratio * 100):>4}'
            )

    if report.strategies_note:
        lines.extend(['', 'Applied view', f'  {report.strategies_note}'])

    lines.extend(_section('Agent view', report.view, empty='(no projected view yet)'))
    lines.extend(_section('Session log', report.log, empty='(empty session)'))

    return '\n'.join(lines)
