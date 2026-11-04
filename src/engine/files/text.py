"""Text operations on files: lines, line endings and decoding.

Everything that knows about ``\\n``, ``\\r\\n`` and UTF-8 lives here. The file tools
only orchestrate; that way line-ending handling can be reasoned about and tested
in isolation from I/O.
"""

from __future__ import annotations

import base64
import codecs

from src.engine.files.models.file_tool_error import FileToolError

PREVIEW_LIMIT = 300
"""Characters kept in a patch preview."""


def decode(data: bytes) -> tuple[str, bool, str]:
    """Returns ``(content, is_binary, encoding)``.

    A byte limit can cut a multibyte sequence in half. That is a truncated text
    file, not a binary one: the incomplete tail is discarded and the readable
    text is kept. Invalid bytes in any other position do mean binary.
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
    """Splits on ``\\n`` keeping the end of each line.

    ``str.splitlines`` also splits on ``\\x0b``, ``\\x0c`` and Unicode
    separators, which would make the line numbers not match the editor.
    Keeping the endings lets a patch return a CRLF file byte for
    byte identical.
    """
    if not text:
        return []
    parts = text.split('\n')
    lines = [part + '\n' for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def strip_eol(value: str) -> str:
    """Removes a single line ending, so ``'a'`` and ``'a\\n'`` compare equal."""
    if value.endswith('\r\n'):
        return value[:-2]
    if value.endswith('\n'):
        return value[:-1]
    return value


def detect_newline(text: str) -> str:
    """Dominant line ending of the file."""
    return '\r\n' if '\r\n' in text else '\n'


def find_occurrences(text: str, needle: str) -> list[int]:
    """Positions of every occurrence of ``needle``."""
    starts: list[int] = []
    index = text.find(needle)
    while index != -1:
        starts.append(index)
        index = text.find(needle, index + 1)
    return starts


def line_of(text: str, index: int) -> int:
    """Line number (1-indexed) of a position in the text."""
    return text.count('\n', 0, index) + 1


def preview(value: str, limit: int = PREVIEW_LIMIT) -> str:
    """One-line summary of a text."""
    collapsed = ' '.join(value.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + '…'


def apply_string_patch(text: str, old: str, new: str, replace_all: bool) -> tuple[str, int]:
    """Replaces ``old`` with ``new``. Returns ``(text, replacements)``."""
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
    """Replaces a line range. Returns ``(text, original region)``."""
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
    # A replacement without a trailing newline still needs one, unless it falls on the
    # last line of a file that does not end with a newline.
    if replacement and not replacement.endswith('\n') and (end_line < total or region.endswith('\n')):
        replacement += newline

    updated = ''.join(lines[: start_line - 1]) + replacement + ''.join(lines[end_line:])
    return updated, region
