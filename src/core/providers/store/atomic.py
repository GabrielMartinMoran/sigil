"""Atomic JSON files with an explicit permission mode.

Every file Sigil owns is written the same way: to a sibling ``.tmp``, with the
final mode applied before the rename, then ``os.replace``. A crash leaves the
previous file intact instead of a half-written one, which matters most for the
credentials file.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

MODE_PRIVATE = 0o600
"""Owner read/write only. Credentials."""

MODE_SHARED = 0o644
"""World-readable metadata. Accounts, state."""


def _current_mode(path: Path) -> int | None:
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def read_json(path: Path) -> dict[str, Any] | None:
    """Reads a JSON object. Returns ``None`` for a missing, unreadable or corrupt file.

    A corrupt file is not an error the caller has to handle: the whole system
    treats "no configuration" as a valid state, so silently degrading to empty
    is the right behaviour and keeps a bad edit from bricking the app.
    """
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path, data: dict[str, Any], *, mode: int = MODE_SHARED) -> None:
    """Writes ``data`` atomically with ``mode``.

    The mode is applied to the temporary file before the rename, so the final
    name never exists with wider permissions, even for an instant.

    Raises:
        OSError: When the directory or the file cannot be written. Callers that
            must not fail (state persistence) catch it and keep going.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.tmp')
    payload = json.dumps(data, ensure_ascii=False, indent=2)

    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(payload)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:  # the temporary file must not survive a Ctrl+C either
        temporary.unlink(missing_ok=True)
        raise

    os.replace(temporary, path)

    # A pre-existing file keeps its old mode across the rename on some
    # platforms; tighten it rather than trusting the rename.
    if _current_mode(path) != mode:
        os.chmod(path, mode)


def tighten(path: Path, mode: int = MODE_PRIVATE) -> None:
    """Applies ``mode`` to an existing file, if it exists."""
    if path.exists():
        current = _current_mode(path)
        if current is not None and current != mode:
            try:
                os.chmod(path, mode)
            except OSError:
                pass
