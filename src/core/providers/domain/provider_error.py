"""The base failure of a provider account."""

from __future__ import annotations


class ProviderError(Exception):
    """Base of every provider failure with a user-facing message."""
