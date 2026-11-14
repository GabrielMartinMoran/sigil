"""Text operations on files: lines, line endings and decoding.

Everything that knows about ``\\n``, ``\\r\\n`` and UTF-8 lives here. The file tools
only orchestrate; that way line-ending handling can be reasoned about and tested
in isolation from I/O.
"""

from __future__ import annotations

import base64
import codecs
from collections.abc import Iterator, Sequence

from src.engine.files.models.file_tool_error import FileToolError

MATCH_LOCATION_LIMIT = 10
"""Maximum locations included in an ambiguous patch diagnostic."""

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


def count_lines(text: str) -> int:
    """Count editor lines without allocating a string per line."""
    return text.count('\n') + int(bool(text) and not text.endswith('\n'))


def line_boundary(text: str, lines_before: int, total: int) -> int:
    """Offset after ``lines_before`` lines, scanning from the nearer end."""
    if lines_before <= 0:
        return 0
    if lines_before >= total:
        return len(text)
    if lines_before <= total - lines_before:
        offset = 0
        for _ in range(lines_before):
            offset = text.index('\n', offset) + 1
        return offset
    offset = len(text) - int(text.endswith('\n'))
    for _ in range(total - lines_before):
        offset = text.rfind('\n', 0, offset)
    return offset + 1


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


def find_occurrences(text: str, needle: str) -> Iterator[int]:
    """Positions of every occurrence, including overlapping matches."""
    index = text.find(needle)
    while index != -1:
        yield index
        index = text.find(needle, index + 1)


def match_locations(text: str, positions: Sequence[int], count: int) -> str:
    """A bounded preview, counting newlines only in consecutive disjoint spans."""
    lines: list[str] = []
    previous = 0
    line = 1
    for position in positions:
        line += text.count('\n', previous, position)
        lines.append(str(line))
        previous = position
    if count > len(positions):
        lines.append(f'… {count - len(positions)} more')
    return ', '.join(lines)


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
    first = next(starts, None)
    if first is None:
        raise FileToolError('`old_string` was not found in the file. Read the file again and copy the exact text.')
    if replace_all:
        # If the first character never recurs, the needle cannot overlap itself.
        count = text.count(old) if old.find(old[0], 1) == -1 else 1 + sum(1 for _ in starts)
        return text.replace(old, new), count

    positions = [first]
    count = 1
    for count, start in enumerate(starts, start=2):
        if len(positions) < MATCH_LOCATION_LIMIT:
            positions.append(start)
    if count > 1:
        lines = match_locations(text, positions, count)
        raise FileToolError(
            f'`old_string` appears {count} times (lines {lines}). '
            'Include more surrounding context to make it unique, or set `replace_all` to true.'
        )
    return text[:first] + new + text[first + len(old) :], 1


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
