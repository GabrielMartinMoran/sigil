"""The DeliveryConflictError value of the accepted-input queue."""

from __future__ import annotations


class DeliveryConflictError(ValueError):
    """A delivery fact does not match the claimed input or its checkpoint."""
