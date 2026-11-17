"""File tools: read, write and patch with validation.

Each tool is thin on purpose: it validates the input, delegates to ``text`` or to the
filesystem, and returns a model. The line and line-ending logic
lives in ``text``; the path logic, in ``paths``.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import stat
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict

from src.core.files.conflicts import same_file_conflict
from src.core.files.limits import MAX_READ_BYTES
from src.core.files.models.file_content import FileContent
from src.core.files.models.file_tool_error import FileToolError
from src.core.files.models.patch_result import PatchResult
from src.core.files.models.write_result import WriteResult
from src.core.files.paths import resolve
from src.core.files.text import (
    apply_line_patch,
    apply_string_patch,
    count_lines,
    decode,
    line_boundary,
    split_lines,
)
from src.core.tooling.compaction_policies import (
    EDIT_FILE_COMPACTOR,
    READ_FILE_COMPACTOR,
)
from src.core.tooling.logging import ToolLogger
from src.core.tooling.presentation import human_bytes
from src.core.tooling.resource_kind import ResourceKind
from src.core.tooling.tool_catalog import ToolCatalog
from src.core.tooling.tool_group import ToolGroup
from src.core.tooling.tool_permission import ToolPermission
from src.core.tooling.tool_registrar import ToolRegistrar
from src.core.tooling.tool_summary import ToolSummary

_READ = ToolPermission(groups=(ToolGroup.READ,), resource='path', kind=ResourceKind.PATH)
_WRITE = ToolPermission(groups=(ToolGroup.WRITE,), resource='path', kind=ResourceKind.PATH)


def describe_read(args: Mapping[str, Any], result: Any) -> ToolSummary | None:
    """How much was read: `120 lines`, `1-40 of 300`, `binary · 4.2 KB`.

    The interesting fact about a read is its size, and whether the range was the
    whole file or a slice: a reader looking at a call needs to know if the model saw
    six lines or six hundred before judging what it does next.
    """
    if not isinstance(result, FileContent):
        return None
    if result.binary:
        return ToolSummary(f'binary · {human_bytes(result.size)}')
    if result.total_lines:
        shown = _line_count(result)
        if result.start_line and result.start_line > 1 or (result.end_line and result.end_line < result.total_lines):
            return ToolSummary(f'{shown} of {result.total_lines} lines')
        return ToolSummary(f'{shown} lines')
    return ToolSummary(human_bytes(result.size))


def _line_count(result: FileContent) -> int:
    """Lines actually returned, counted from the content and not from the range."""
    return len(split_lines(result.content)) if result.content else 0


def describe_write(args: Mapping[str, Any], result: Any) -> ToolSummary | None:
    """What the write did: `created · 1.2 KB`, `appended · 240 B`, `written · 4 KB`."""
    if not isinstance(result, WriteResult):
        return None
    if result.appended:
        verb = 'appended'
    elif result.created:
        verb = 'created'
    else:
        verb = 'written'
    return ToolSummary(f'{verb} · {human_bytes(result.bytes_written)}')


def describe_patch(args: Mapping[str, Any], result: Any) -> ToolSummary | None:
    """How many edits landed: `3 replacements`.

    Not a line delta. `PatchResult` carries byte counts, not line counts, and counting
    lines per call would mean diffing the file inside the tool — the repo-level
    `DiffStat` already answers "how much did this turn change" better than a per-call
    line count would.
    """
    if not isinstance(result, PatchResult):
        return None
    count = result.replacements
    if count == 0:
        return ToolSummary('no replacement')
    return ToolSummary(f'{count} replacement' + ('s' if count != 1 else ''))


def _read_line_range(target: Any, path: str, size: int, start_line: int | None, end_line: int | None) -> FileContent:
    """Reads a line range from a text file."""
    try:
        # newline='' preserves CRLF so the text can be copied as is into
        # `write_file`.
        text = target.read_text(encoding='utf-8', newline='')
    except UnicodeDecodeError as exc:
        raise FileToolError(f'File {path!r} is not UTF-8 text; read it without a line range.') from exc

    total = count_lines(text)
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
        content=text[line_boundary(text, first - 1, total) : line_boundary(text, last, total)],
        start_line=first,
        end_line=last,
        total_lines=total,
    )


def _read_whole(target: Any, size: int, max_bytes: int) -> FileContent:
    """Reads the whole file, or its first ``max_bytes``."""
    if max_bytes < 0:
        # Preserve the existing negative-slice semantics for direct callers.
        raw = target.read_bytes()
    else:
        with target.open('rb') as handle:
            raw = handle.read(max_bytes + 1)
    content, binary, encoding = decode(raw[:max_bytes])
    return FileContent(
        path=str(target),
        size=size,
        truncated=len(raw) > max_bytes,
        binary=binary,
        encoding=encoding,
        content=content,
    )


def _require_file(path: str, *, allow_external: bool = False, root: Path | None = None) -> Any:
    """Resolves ``path`` and validates that it is an existing file."""
    target = resolve(path, allow_external=allow_external, root=root)
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


class WriteOperation(TypedDict):
    type: Literal['write', 'append']
    content: str


class ReplaceOperation(TypedDict):
    type: Literal['replace']
    old: str
    new: str
    replace_all: NotRequired[bool]


class ReplaceLinesOperation(TypedDict):
    type: Literal['replace_lines']
    start_line: int
    end_line: int
    new: str
    expected_text: NotRequired[str | None]


EditOperation = WriteOperation | ReplaceOperation | ReplaceLinesOperation


def _apply_edit_operations(text: str | None, operations: list[EditOperation]) -> str:
    if not operations:
        raise FileToolError('Provide at least one operation.')
    for index, operation in enumerate(operations, 1):
        try:
            kind = operation['type']
            if kind == 'write':
                text = operation['content']
            elif kind == 'append':
                text = (text or '') + operation['content']
            elif text is None:
                raise FileToolError('The file does not exist; use write or append first.')
            elif kind == 'replace':
                if 'expected_text' in operation:
                    raise FileToolError('expected_text is only valid for replace_lines.')
                text, _ = apply_string_patch(
                    text, operation['old'], operation['new'], operation.get('replace_all', False)
                )
            elif kind == 'replace_lines':
                text, _ = apply_line_patch(
                    text,
                    operation['start_line'],
                    operation['end_line'],
                    operation['new'],
                    operation.get('expected_text'),
                )
            else:
                raise FileToolError(f'Unknown operation: {kind!r}.')
        except FileToolError as exc:
            raise FileToolError(f'Operation {index}: {exc}') from exc
    assert text is not None
    return text


def _preserve_metadata(target: Path, fd: int, existing: os.stat_result) -> None:
    """Copy security-relevant metadata before replacing the original inode."""
    if not all(hasattr(os, name) for name in ('listxattr', 'getxattr', 'setxattr', 'removexattr')):
        raise FileToolError('Cannot verify extended attributes on this platform; file not changed.')
    try:
        current = os.fstat(fd)
        if (current.st_uid, current.st_gid) != (existing.st_uid, existing.st_gid):
            os.fchown(fd, existing.st_uid, existing.st_gid)
        os.fchmod(fd, stat.S_IMODE(existing.st_mode))
        attributes = {name: os.getxattr(target, name) for name in os.listxattr(target)}
        for name in set(os.listxattr(fd)) - attributes.keys():
            os.removexattr(fd, name)
        for name, value in attributes.items():
            os.setxattr(fd, name, value)
        if stat.S_IMODE(os.fstat(fd).st_mode) != stat.S_IMODE(existing.st_mode):
            raise OSError('File mode changed while copying metadata')
    except OSError as exc:
        raise FileToolError(f'Cannot preserve file metadata; file not changed: {exc}') from exc


def _write_atomically(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    existing = target.stat() if target.exists() else None
    if existing is not None and existing.st_nlink > 1:
        raise FileToolError('Cannot atomically edit a file with multiple hard links.')
    mode = stat.S_IMODE(existing.st_mode) if existing is not None else None
    name = str(target.parent / f'.{target.name}.{secrets.token_hex(16)}')
    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600 if mode is not None else 0o666)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
            handle.flush()
            if existing is not None:
                _preserve_metadata(target, handle.fileno(), existing)
        os.replace(name, target)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def register_file_tools(
    agent: Any,
    logger: ToolLogger | None = None,
    catalog: ToolCatalog | None = None,
    *,
    allow_external: Callable[[], bool] = lambda: False,
    root: Callable[[], Path] | None = None,
) -> None:
    """Registers the file tools on a pydantic-ai agent."""
    tools = ToolRegistrar(agent, logger, catalog)

    @tools.tool(permission=_READ, describe=describe_read, compaction=READ_FILE_COMPACTOR)
    async def read_file(
        path: str,
        start_line: int | None = None,
        end_line: int | None = None,
        max_bytes: int = MAX_READ_BYTES,
    ) -> FileContent:
        """Read a text or binary file from the workspace or the temporary directory.

        Use this to inspect a file before editing it. Paths are resolved
        against the workspace; the OS temporary directory is allowed too. Other paths require session authorization via `/external-files allow`. For large files, request a line range or a byte
        limit.

        Args:
            path: File path, absolute or relative to the workspace.
            start_line: Optional first line to return (1-indexed, inclusive).
            end_line: Optional last line to return (1-indexed, inclusive).
            max_bytes: Maximum bytes to read when no line range is given
                (default 256 KiB).
        """
        external = allow_external()
        workspace = None if root is None else root()

        def read() -> FileContent:
            target = _require_file(path, allow_external=external, root=workspace)
            size = target.stat().st_size
            if start_line is not None or end_line is not None:
                return _read_line_range(target, path, size, start_line, end_line)
            return _read_whole(target, size, max_bytes)

        return await asyncio.to_thread(read)

    @tools.tool(
        concurrency=lambda ctx: same_file_conflict(ctx, None if root is None else root()),
        permission=_WRITE,
        compaction=EDIT_FILE_COMPACTOR,
    )
    async def write_file(path: str, operations: list[EditOperation]) -> dict[str, Any]:
        """Create or edit one text file with ordered operations.

        Supported types are write, append, replace, and replace_lines. Read existing
        content first and prefer targeted replacements. A write operation replaces
        the entire content; use it for new files or intentional full rewrites.
        Replace requires a unique exact match unless replace_all is true; use an
        empty replacement to delete text. Line ranges are 1-indexed and inclusive,
        evaluated against the content produced by preceding operations.
        Group dependent edits to this file in one call: if any operation fails,
        nothing is written. Separate files are not a transaction. After a conflict
        or uncertain result, inspect current content before retrying edits.

        Args:
            path: File path, absolute or relative to the workspace.
            operations: Non-empty list of operations to apply in order.
        """
        external = allow_external()
        workspace = None if root is None else root()

        def write() -> dict[str, Any]:
            target = resolve(path, allow_external=external, root=workspace)
            exists = target.exists()
            if exists and not target.is_file():
                raise FileToolError(f'Not a file: {path!r}.')
            replaces_all = bool(operations) and operations[0]['type'] == 'write'
            text = _read_text_for_patch(target, path) if exists and not replaces_all else None
            updated = _apply_edit_operations(text, operations)
            _write_atomically(target, updated)
            return {
                'path': str(target),
                'created': not exists,
                'operations': len(operations),
                'bytes_after': len(updated.encode('utf-8')),
            }

        return await asyncio.to_thread(write)
