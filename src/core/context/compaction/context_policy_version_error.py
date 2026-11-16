"""The ContextPolicyVersionError value of the context layer."""

from __future__ import annotations


class ContextPolicyVersionError(ValueError):
    """A newer policy record needs an explicit migration, not silent reinterpretation."""
