"""The NoProviderError value of the providers layer."""

from __future__ import annotations


class NoProviderError(Exception):
    """Raised when a turn is attempted with no configured provider."""
