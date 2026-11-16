"""The PlanTransportError value of the providers layer."""

from __future__ import annotations


class PlanTransportError(RuntimeError):
    """The plan's client could not be built. The caller falls back to the plain path."""
