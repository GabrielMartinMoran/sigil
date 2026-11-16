"""The InputConflictError value of the accepted-input queue."""

from __future__ import annotations


class InputConflictError(ValueError):
    """A request identifier was reused with different content."""
