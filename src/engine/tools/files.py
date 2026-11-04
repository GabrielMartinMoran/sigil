"""Tools de archivos: leer, escribir y parchear con validación."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.engine.tools.tool_registry import ToolLogger, ToolRegistrar

MAX_READ_BYTES = 256 * 1024


class FileToolError(Exception):
    """Error de archivo con un mensaje accionable."""


class FileContent(BaseModel):
    """Contenido leído de un archivo."""

    path: str
    size: int
    truncated: bool
    binary: bool
    encoding: str = Field(description='utf-8 for text, base64 for binary content.')
    content: str
    start_line: int | None = None
    end_line: int | None = None
    total_lines: int | None = None


class WriteResult(BaseModel):
    """Resultado de escribir un archivo."""

    path: str
    bytes_written: int
    created: bool
    appended: bool


class PatchResult(BaseModel):
    """Resultado de parchear un archivo."""

    path: str
    mode: str = Field(description='`string` or `lines`.')
    replacements: int
    bytes_before: int
    bytes_after: int
    preview: str


def _workspace_root() -> Path:
    return Path.cwd().resolve()


def _resolve(path: str) -> Path:
    root = _workspace_root()
    candidate = Path(path).resolve() if os.path.isabs(path) else (root / path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise FileToolError(
            f'Path {path!r} is outside the workspace ({root}). '
            'Files outside the workspace need explicit authorization from the user.'
        ) from None
    return candidate


def _decode(data: bytes) -> tuple[str, bool, str]:
    if b'\x00' in data:
        return base64.b64encode(data).decode('ascii'), True, 'base64'
    try:
        return data.decode('utf-8'), False, 'utf-8'
    except UnicodeDecodeError:
        return base64.b64encode(data).decode('ascii'), True, 'base64'


def _find_occurrences(text: str, needle: str) -> list[int]:
    starts: list[int] = []
    index = text.find(needle)
    while index != -1:
        starts.append(index)
        index = text.find(needle, index + 1)
    return starts


def _line_of(text: str, index: int) -> int:
    return text.count('\n', 0, index) + 1


def _detect_newline(text: str) -> str:
    return '\r\n' if '\r\n' in text else '\n'


def _apply_string_patch(text: str, old: str, new: str, replace_all: bool) -> tuple[str, int]:
    if old == '':
        raise FileToolError('`old_string` cannot be empty.')
    starts = _find_occurrences(text, old)
    if not starts:
        raise FileToolError('`old_string` was not found in the file. Read the file again and copy the exact text.')
    if len(starts) > 1 and not replace_all:
        lines = ', '.join(str(_line_of(text, start)) for start in starts)
        raise FileToolError(
            f'`old_string` appears {len(starts)} times (lines {lines}). '
            'Include more surrounding context to make it unique, or set `replace_all` to true.'
        )
    if replace_all:
        return text.replace(old, new), len(starts)
    start = starts[0]
    return text[:start] + new + text[start + len(old) :], 1


def _apply_line_patch(
    text: str,
    start_line: int,
    end_line: int,
    new: str,
    expected_text: str | None,
    expected_sha256: str | None,
) -> tuple[str, str]:
    newline = _detect_newline(text)
    had_trailing = text.endswith(newline)
    lines = text.split(newline)
    if had_trailing and lines and lines[-1] == '':
        lines = lines[:-1]
    total = len(lines)
    if not (1 <= start_line <= end_line <= total):
        raise FileToolError(f'Invalid line range {start_line}-{end_line} for a file with {total} lines.')

    region_lines = lines[start_line - 1 : end_line]
    region = newline.join(region_lines)
    if end_line < total or had_trailing:
        region += newline

    if expected_text is not None and region != expected_text:
        raise FileToolError(
            'The current text in the requested line range does not match `expected_text`. '
            'Read the file again and retry with the correct range or text.'
        )
    if expected_sha256 is not None:
        actual = hashlib.sha256(region.encode('utf-8')).hexdigest()
        if actual != expected_sha256.lower():
            raise FileToolError(
                f'The current text in the requested line range does not match `expected_sha256`. Actual hash: {actual}.'
            )

    replacement = new.replace('\r\n', '\n').replace('\n', newline) if new else ''
    new_lines = replacement.split(newline) if replacement else []
    if new_lines and new_lines[-1] == '':
        new_lines = new_lines[:-1]

    updated = newline.join(lines[: start_line - 1] + new_lines + lines[end_line:])
    if had_trailing and updated:
        updated += newline
    return updated, region


def _preview(value: str, limit: int = 300) -> str:
    collapsed = ' '.join(value.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + '…'


def register_file_tools(agent: Any, logger: ToolLogger | None = None) -> None:
    """Registra las tools de archivos en un agente de pydantic-ai."""
    tools = ToolRegistrar(agent, logger)

    @tools.tool
    def read_file(
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        max_bytes: int = MAX_READ_BYTES,
    ) -> FileContent:
        """Read a text or binary file from the workspace.

        Use this to inspect a file before editing it. Paths are resolved
        against the workspace; paths outside it are rejected. For large files,
        request a line range or a byte limit.

        Args:
            path: File path, absolute or relative to the workspace.
            start_line: Optional first line to return (1-indexed, inclusive).
            end_line: Optional last line to return (1-indexed, inclusive).
            max_bytes: Maximum bytes to read when no line range is given
                (default 256 KiB).
        """
        target = _resolve(path)
        if not target.exists():
            raise FileToolError(f'File not found: {path!r}.')
        if not target.is_file():
            raise FileToolError(f'Not a file: {path!r}.')
        size = target.stat().st_size

        if start_line is not None or end_line is not None:
            try:
                text = target.read_text(encoding='utf-8')
            except UnicodeDecodeError as exc:
                raise FileToolError(f'File {path!r} is not UTF-8 text; read it without a line range.') from exc
            lines = text.splitlines()
            total = len(lines)
            first = max(1, start_line or 1)
            last = min(total, end_line or total)
            if first > last:
                raise FileToolError(f'Invalid line range {first}-{last} for a file with {total} lines.')
            chunk = '\n'.join(lines[first - 1 : last])
            if text.endswith('\n'):
                chunk += '\n'
            return FileContent(
                path=str(target),
                size=size,
                truncated=False,
                binary=False,
                encoding='utf-8',
                content=chunk,
                start_line=first,
                end_line=last,
                total_lines=total,
            )

        raw = target.read_bytes()
        truncated = len(raw) > max_bytes
        content, binary, encoding = _decode(raw[:max_bytes])
        return FileContent(
            path=str(target),
            size=size,
            truncated=truncated,
            binary=binary,
            encoding=encoding,
            content=content,
        )

    @tools.tool
    def write_file(path: str, content: str, append: bool = False) -> WriteResult:
        """Write or append to a text file in the workspace.

        Parent directories are created as needed. Use `patch_file` for focused
        edits to an existing file; use this tool to create a new file or to
        replace/append its full content.

        Args:
            path: File path, absolute or relative to the workspace.
            content: Text to write.
            append: When true, append instead of overwriting.
        """
        target = _resolve(path)
        created = not target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode('utf-8')
        mode = 'ab' if append else 'wb'
        with target.open(mode) as handle:
            handle.write(data)
        return WriteResult(path=str(target), bytes_written=len(data), created=created, appended=append)

    @tools.tool
    def patch_file(
        path: str,
        old_string: str | None = None,
        new_string: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        expected_text: str | None = None,
        expected_sha256: str | None = None,
        replace_all: bool = False,
    ) -> PatchResult:
        """Edit an existing file, with two modes.

        Prefer the exact-match mode: pass `old_string` (text to find) and
        `new_string` (replacement). The old text must appear exactly once unless
        `replace_all` is true. This is the most robust mode and what agents like
        OpenCode and Claude Code use.

        Alternatively, pass `start_line` and `end_line` (1-indexed, inclusive)
        and `new_string` to replace those lines. Optionally validate the current
        content with `expected_text` or `expected_sha256` (SHA-256 hex of the
        region). This mode is useful when you know the line numbers.

        Use `write_file` to create a new file; this tool only edits existing
        files.

        Args:
            path: File path, absolute or relative to the workspace.
            old_string: Exact text to find (string mode).
            new_string: Replacement text (required in both modes).
            start_line: First line to replace (line mode).
            end_line: Last line to replace (line mode).
            expected_text: Optional exact text of the target region, for
                validation in line mode.
            expected_sha256: Optional SHA-256 hex of the target region, for
                validation in line mode.
            replace_all: Replace every occurrence in string mode.
        """
        target = _resolve(path)
        if not target.exists():
            raise FileToolError(f'File not found: {path!r}. Use `write_file` to create it.')
        if not target.is_file():
            raise FileToolError(f'Not a file: {path!r}.')

        try:
            text = target.read_text(encoding='utf-8')
        except UnicodeDecodeError as exc:
            raise FileToolError(f'File {path!r} is not UTF-8 text.') from exc

        before = len(text.encode('utf-8'))
        line_mode = start_line is not None or end_line is not None

        if old_string is not None:
            if new_string is None:
                raise FileToolError('`new_string` is required when `old_string` is provided.')
            if line_mode:
                raise FileToolError('Use either `old_string`/`new_string` or `start_line`/`end_line`, not both.')
            updated, count = _apply_string_patch(text, old_string, new_string, replace_all)
            mode = 'string'
        else:
            if not line_mode:
                raise FileToolError('Provide `old_string`/`new_string` or `start_line`/`end_line` with `new_string`.')
            if new_string is None:
                raise FileToolError('`new_string` is required for line-range patches.')
            if start_line is None or end_line is None:
                raise FileToolError('Both `start_line` and `end_line` are required for line-range patches.')
            updated, _region = _apply_line_patch(text, start_line, end_line, new_string, expected_text, expected_sha256)
            count = 1
            mode = 'lines'

        target.write_text(updated, encoding='utf-8')
        return PatchResult(
            path=str(target),
            mode=mode,
            replacements=count,
            bytes_before=before,
            bytes_after=len(updated.encode('utf-8')),
            preview=_preview(new_string or ''),
        )


__all__ = [
    'FileContent',
    'FileToolError',
    'PatchResult',
    'WriteResult',
    'register_file_tools',
]
