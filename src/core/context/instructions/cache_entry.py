"""The CacheEntry value of the context layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """Content read from a file, with the invalidation key."""

    mtime_ns: int
    size: int
    content: str
    truncated: bool
