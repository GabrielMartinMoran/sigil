"""Reasoning effort values the provider accepts."""

from __future__ import annotations

EFFORTS: frozenset[str] = frozenset({'minimal', 'low', 'medium', 'high', 'xhigh', 'max'})
"""Valid efforts. Any other value is sent without `thinking`."""
