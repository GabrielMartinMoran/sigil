"""File tools: read, write and patch with validation.

Each tool is thin on purpose: it validates the input, delegates to ``text`` or to the
filesystem, and returns a model. The line and line-ending logic
lives in ``text``; the path logic, in ``paths``.
"""

from __future__ import annotations

from typing import Any

from src.engine.files.conflicts import same_file_conflict
from src.engine.files.limits import MAX_READ_BYTES
from src.engine.files.models.file_content import FileContent
from src.engine.files.models.file_tool_error import FileToolError
from src.engine.files.models.patch_result import PatchResult
from src.engine.files.models.write_result import WriteResult
from src.engine.files.paths import resolve
from src.engine.files.text import (
    apply_line_patch,
    apply_string_patch,
    decode,
    preview,
    split_lines,
)
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.permission import ResourceKind, ToolGroup, ToolPermission
from src.engine.tooling.registrar import ToolRegistrar

_READ = ToolPermission(groups=(ToolGroup.READ,), resource='path', kind=ResourceKind.PATH)
_WRITE = ToolPermission(groups=(ToolGroup.WRITE,), resource='path', kind=ResourceKind.PATH)


def _read_line_range(target: Any, path: str, size: int, start_line: int | None, end_line: int | None) -> FileContent:
    """Reads a line range from a text file."""
    try:
        # newline='' preserves CRLF so the text can be copied as is into
        # `patch_file`.
        text = target.read_text(encoding='utf-8', newline='')
    except UnicodeDecodeError as exc:
        raise FileToolError(f'File {path!r} is not UTF-8 text; read it without a line range.') from exc

    lines = split_lines(text)
    total = len(lines)
    first = max(1, start_line or 1)
    last = min(total, end_line or total)
    if first > last:
        raise FileToolError(f'Invalid line range {first}-{last} for a file with {total} lines.')

    return FileContent(
        path=str(target),
        size=size,
        truncated=False,
        binary=False,
        encoding='utf-8',
        content=''.join(lines[first - 1 : last]),
        start_line=first,
        end_line=last,
        total_lines=total,
    )


def _read_whole(target: Any, size: int, max_bytes: int) -> FileContent:
    """Reads the whole file, or its first ``max_bytes``."""
    raw = target.read_bytes()
    content, binary, encoding = decode(raw[:max_bytes])
    return FileContent(
        path=str(target),
        size=size,
        truncated=len(raw) > max_bytes,
        binary=binary,
        encoding=encoding,
        content=content,
    )


def _require_file(path: str) -> Any:
    """Resolves ``path`` and validates that it is an existing file."""
    target = resolve(path)
    if not target.exists():
        raise FileToolError(f'File not found: {path!r}.')
    if not target.is_file():
        raise FileToolError(f'Not a file: {path!r}.')
    return target


def _read_text_for_patch(target: Any, path: str) -> str:
    """Reads a file's text preserving its line endings."""
    try:
        # newline='' avoids silently rewriting a CRLF file to LF.
        return target.read_text(encoding='utf-8', newline='')
    except UnicodeDecodeError as exc:
        raise FileToolError(f'File {path!r} is not UTF-8 text.') from exc


def _patched_text(
    text: str,
    *,
    old_string: str | None,
    new_string: str | None,
    start_line: int | None,
    end_line: int | None,
    expected_text: str | None,
    replace_all: bool,
) -> tuple[str, str, int]:
    """Applies the requested patch. Returns ``(text, mode, replacements)``."""
    line_mode = start_line is not None or end_line is not None

    if old_string is not None:
        if new_string is None:
            raise FileToolError('`new_string` is required when `old_string` is provided.')
        if line_mode:
            raise FileToolError('Use either `old_string`/`new_string` or `start_line`/`end_line`, not both.')
        if expected_text is not None:
            raise FileToolError('`expected_text` only applies to line-range patches, not to `old_string`.')
        updated, count = apply_string_patch(text, old_string, new_string, replace_all)
        return updated, 'string', count

    if not line_mode:
        raise FileToolError('Provide `old_string`/`new_string` or `start_line`/`end_line` with `new_string`.')
    if new_string is None:
        raise FileToolError('`new_string` is required for line-range patches.')
    if start_line is None or end_line is None:
        raise FileToolError('Both `start_line` and `end_line` are required for line-range patches.')

    updated, _region = apply_line_patch(text, start_line, end_line, new_string, expected_text)
    return updated, 'lines', 1


def register_file_tools(agent: Any, logger: ToolLogger | None = None) -> None:
    """Registers the file tools on a pydantic-ai agent."""
    tools = ToolRegistrar(agent, logger)

    @tools.tool(permission=_READ)
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
        target = _require_file(path)
        size = target.stat().st_size
        if start_line is not None or end_line is not None:
            return _read_line_range(target, path, size, start_line, end_line)
        return _read_whole(target, size, max_bytes)

    @tools.tool(concurrency=same_file_conflict, permission=_WRITE)
    def write_file(path: str, content: str, append: bool = False) -> WriteResult:
        """Write or append to a text file in the workspace.

        Parent directories are created as needed. Use `patch_file` for focused
        edits to an existing file; use this tool to create a new file or to
        replace/append its full content.

        Two calls that write the same file in the same response conflict: the
        later one fails and must be re-issued in a separate step.

        Args:
            path: File path, absolute or relative to the workspace.
            content: Text to write.
            append: When true, append instead of overwriting.
        """
        target = resolve(path)
        created = not target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        data = content.encode('utf-8')
        with target.open('ab' if append else 'wb') as handle:
            handle.write(data)
        return WriteResult(path=str(target), bytes_written=len(data), created=created, appended=append)

    @tools.tool(concurrency=same_file_conflict, permission=_WRITE)
    def patch_file(
        path: str,
        old_string: str | None = None,
        new_string: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        expected_text: str | None = None,
        replace_all: bool = False,
    ) -> PatchResult:
        """Edit an existing file, with two modes.

        Prefer the exact-match mode: pass `old_string` (text to find) and
        `new_string` (replacement). The old text must appear exactly once unless
        `replace_all` is true. This is the most robust mode and what agents like
        OpenCode and Claude Code use.

        Alternatively, pass `start_line` and `end_line` (1-indexed, inclusive)
        and `new_string` to replace those lines. Optionally validate the current
        content with `expected_text` (the text you expect in that range, as
        returned by `read_file`; a trailing newline is ignored). This mode is
        useful when you know the line numbers.

        Use `write_file` to create a new file; this tool only edits existing
        files.

        Two calls that edit the same file in the same response conflict: the
        later one fails and must be re-issued in a separate step.

        Args:
            path: File path, absolute or relative to the workspace.
            old_string: Exact text to find (string mode).
            new_string: Replacement text (required in both modes).
            start_line: First line to replace (line mode).
            end_line: Last line to replace (line mode).
            expected_text: Optional exact text of the target region, for
                validation in line mode.
            replace_all: Replace every occurrence in string mode.
        """
        target = _require_file(path)
        text = _read_text_for_patch(target, path)
        before = len(text.encode('utf-8'))

        updated, mode, count = _patched_text(
            text,
            old_string=old_string,
            new_string=new_string,
            start_line=start_line,
            end_line=end_line,
            expected_text=expected_text,
            replace_all=replace_all,
        )

        target.write_text(updated, encoding='utf-8', newline='')
        return PatchResult(
            path=str(target),
            mode=mode,
            replacements=count,
            bytes_before=before,
            bytes_after=len(updated.encode('utf-8')),
            preview=preview(new_string or ''),
        )
