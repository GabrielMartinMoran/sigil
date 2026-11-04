"""Valores de esfuerzo de razonamiento que acepta el provider."""

from __future__ import annotations

EFFORTS: frozenset[str] = frozenset({'minimal', 'low', 'medium', 'high', 'xhigh', 'max'})
"""Esfuerzos válidos. Cualquier otro valor se manda sin `thinking`."""
