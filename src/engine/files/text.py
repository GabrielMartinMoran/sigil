"""Operaciones de texto sobre archivos: líneas, finales de línea y decodificación.

Todo lo que sabe de ``\\n``, ``\\r\\n`` y UTF-8 vive acá. Las tools de archivos
sólo orquestan; así el manejo de finales de línea se puede razonar y testear
aislado del I/O.
"""

from __future__ import annotations

import base64
import codecs

from src.engine.files.models.file_tool_error import FileToolError

PREVIEW_LIMIT = 300
"""Caracteres que se conservan en el preview de un parche."""


def decode(data: bytes) -> tuple[str, bool, str]:
    """Devuelve ``(contenido, es_binario, encoding)``.

    Un límite de bytes puede cortar una secuencia multibyte al medio. Eso es un
    archivo de texto truncado, no binario: se descarta la cola incompleta y se
    conserva el texto legible. Bytes inválidos en cualquier otra posición sí
    significan binario.
    """
    if b'\x00' in data:
        return base64.b64encode(data).decode('ascii'), True, 'base64'
    try:
        return data.decode('utf-8'), False, 'utf-8'
    except UnicodeDecodeError:
        decoder = codecs.getincrementaldecoder('utf-8')()
        try:
            return decoder.decode(data, final=False), False, 'utf-8'
        except UnicodeDecodeError:
            return base64.b64encode(data).decode('ascii'), True, 'base64'


def split_lines(text: str) -> list[str]:
    """Corta por ``\\n`` conservando el final de cada línea.

    ``str.splitlines`` también corta en ``\\x0b``, ``\\x0c`` y separadores
    Unicode, lo que haría que los números de línea no coincidan con el editor.
    Conservar los finales permite que un parche devuelva un archivo CRLF byte a
    byte igual.
    """
    if not text:
        return []
    parts = text.split('\n')
    lines = [part + '\n' for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def strip_eol(value: str) -> str:
    """Quita un único final de línea, para que ``'a'`` y ``'a\\n'`` comparen igual."""
    if value.endswith('\r\n'):
        return value[:-2]
    if value.endswith('\n'):
        return value[:-1]
    return value


def detect_newline(text: str) -> str:
    """Final de línea dominante del archivo."""
    return '\r\n' if '\r\n' in text else '\n'


def find_occurrences(text: str, needle: str) -> list[int]:
    """Posiciones de todas las apariciones de ``needle``."""
    starts: list[int] = []
    index = text.find(needle)
    while index != -1:
        starts.append(index)
        index = text.find(needle, index + 1)
    return starts


def line_of(text: str, index: int) -> int:
    """Número de línea (1-indexado) de una posición del texto."""
    return text.count('\n', 0, index) + 1


def preview(value: str, limit: int = PREVIEW_LIMIT) -> str:
    """Resumen de una línea de un texto."""
    collapsed = ' '.join(value.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + '…'


def apply_string_patch(text: str, old: str, new: str, replace_all: bool) -> tuple[str, int]:
    """Reemplaza ``old`` por ``new``. Devuelve ``(texto, reemplazos)``."""
    if old == '':
        raise FileToolError('`old_string` cannot be empty.')

    starts = find_occurrences(text, old)
    if not starts:
        raise FileToolError('`old_string` was not found in the file. Read the file again and copy the exact text.')
    if len(starts) > 1 and not replace_all:
        lines = ', '.join(str(line_of(text, start)) for start in starts)
        raise FileToolError(
            f'`old_string` appears {len(starts)} times (lines {lines}). '
            'Include more surrounding context to make it unique, or set `replace_all` to true.'
        )
    if replace_all:
        return text.replace(old, new), len(starts)

    start = starts[0]
    return text[:start] + new + text[start + len(old) :], 1


def apply_line_patch(
    text: str,
    start_line: int,
    end_line: int,
    new: str,
    expected_text: str | None,
) -> tuple[str, str]:
    """Reemplaza un rango de líneas. Devuelve ``(texto, región original)``."""
    newline = detect_newline(text)
    lines = split_lines(text)
    total = len(lines)
    if not (1 <= start_line <= end_line <= total):
        raise FileToolError(f'Invalid line range {start_line}-{end_line} for a file with {total} lines.')

    region = ''.join(lines[start_line - 1 : end_line])
    if expected_text is not None and strip_eol(region) != strip_eol(expected_text):
        raise FileToolError(
            'The current text in the requested line range does not match `expected_text`. '
            'Read the file again and retry with the correct range or text.'
        )

    replacement = new.replace('\r\n', '\n').replace('\n', newline) if new else ''
    # Un reemplazo sin salto final igual necesita uno, salvo que caiga en la
    # última línea de un archivo que no termina en salto.
    if replacement and not replacement.endswith('\n') and (end_line < total or region.endswith('\n')):
        replacement += newline

    updated = ''.join(lines[: start_line - 1]) + replacement + ''.join(lines[end_line:])
    return updated, region
