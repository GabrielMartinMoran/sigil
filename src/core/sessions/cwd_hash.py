"""Stable identity of a working directory."""

from __future__ import annotations

import os
from pathlib import Path


def cwd_hash(cwd: str | Path) -> str:
    """Kept for callers that still hash a directory. The database keys by path."""
    import hashlib

    real = os.path.realpath(str(cwd))
    return hashlib.sha256(real.encode('utf-8')).hexdigest()[:16]
