"""The ThemeError value of the core layer."""

from __future__ import annotations


class ThemeError(ValueError):
    """A theme file that cannot be used, with the reason in the message."""
