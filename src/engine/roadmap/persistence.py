"""Roadmap persistence on disk: one JSON file per session."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.engine.roadmap.models.todo_item import TodoItem


def default_directory() -> Path:
    """Roadmap directory of the current workspace."""
    cwd_hash = hashlib.sha256(str(Path.cwd().resolve()).encode('utf-8')).hexdigest()[:16]
    return Path.home() / '.sigil' / 'roadmaps' / cwd_hash


def path_for(directory: Path, session_id: str | None) -> Path | None:
    return directory / f'{session_id}.json' if session_id else None


def load_items(path: Path | None) -> tuple[list[TodoItem], str | None]:
    """Reads the tasks from the file. Returns ``(items, error)``.

    A missing file is not an error; an unreadable or corrupt file is, but
    it is reported without aborting: the in-memory state remains valid.
    """
    if path is None:
        return [], None
    try:
        raw = path.read_text(encoding='utf-8')
    except FileNotFoundError:
        return [], None
    except OSError as exc:
        return [], str(exc)
    try:
        data = json.loads(raw)
        return [TodoItem(**item) for item in data.get('items', [])], None
    except (ValueError, TypeError) as exc:
        return [], f'Could not parse {path}: {exc}'


def save_items(path: Path | None, items: list[TodoItem]) -> str | None:
    """Writes the tasks. Returns the error, or ``None`` if it went well."""
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {'items': [item.model_dump() for item in items]}
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
    except OSError as exc:
        return str(exc)
    return None
