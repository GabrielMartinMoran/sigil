"""Server ownership failure with a message meant for the user."""

from __future__ import annotations


class OwnershipError(RuntimeError):
    """Another server owns the store, or the lock path is not safe to use."""
