"""Concurrency guard for the tools that write files.

Each call reads the file, computes the result and writes it. If two run at
the same time on the same file, the last one to write erases the work of the
other. The first of the batch goes through; the following ones fail with a message that tells
the model how to proceed.
"""

from __future__ import annotations

from src.engine.files.models.file_tool_error import FileToolError
from src.engine.files.paths import resolve
from src.engine.tooling.concurrency import ConcurrencyContext

MUTATING_TOOLS = frozenset({'patch_file', 'write_file'})
"""Tools that rewrite a file and therefore cannot overlap."""


def _target_of(args: dict[str, object]) -> object | None:
    """Resolved path of a call, or ``None`` if it cannot be resolved."""
    try:
        return resolve(str(args['path']))
    except FileToolError, KeyError:
        # Invalid or missing path: the tool will fail with a better message.
        return None


def same_file_conflict(ctx: ConcurrencyContext) -> str | None:
    """Two writes to the same file in the same response would stomp on each other."""
    target = _target_of(ctx.args)
    if target is None:
        return None

    for sibling in ctx.siblings:
        if sibling.tool_name not in MUTATING_TOOLS:
            continue
        if _target_of(sibling.args) == target:
            return (
                f'Another {sibling.tool_name} call in this same response already edits {str(target)!r}. '
                'Concurrent edits to one file overwrite each other, so this call was not applied. '
                'Re-issue it in a separate step, after the earlier edit lands.'
            )
    return None
