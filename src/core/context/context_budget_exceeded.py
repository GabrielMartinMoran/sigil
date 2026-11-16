"""The error raised when the context budget is exhausted."""

from __future__ import annotations


class ContextBudgetExceeded(ValueError):
    """The protected request cannot fit; never send it to the provider."""
