"""The DialectUnsupportedError value of the providers layer."""

from __future__ import annotations


class DialectUnsupportedError(Exception):
    """The provider needs an SDK that is not installed."""
