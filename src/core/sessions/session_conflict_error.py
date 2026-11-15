"""The SessionConflictError value used by the session store."""

from __future__ import annotations


class SessionConflictError(RuntimeError):
    """Another process changed this session; reload it before saving."""
