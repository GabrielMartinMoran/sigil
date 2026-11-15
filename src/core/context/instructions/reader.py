"""Cached read of an instruction file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Content read from a file, with the invalidation key."""

    mtime_ns: int
    size: int
    content: str
    truncated: bool


def read_cached(path: Path, *, max_chars: int, cache: dict[Path, CacheEntry]) -> CacheEntry | None:
    """Reads a file cached by ``(mtime, size)``."""
    try:
        stat = path.stat()
    except OSError:
        cache.pop(path, None)
        return None

    cached = cache.get(path)
    if cached is not None and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
        return cached

    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None

    truncated = len(text) > max_chars
    if truncated:
        text = text[:max_chars]
    entry = CacheEntry(mtime_ns=stat.st_mtime_ns, size=stat.st_size, content=text, truncated=truncated)
    cache[path] = entry
    return entry
