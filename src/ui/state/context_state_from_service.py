"""Helpers that used to live in sidebar_mixin."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from src.core.context.meter.session_cost import SessionCost
from src.ui.widgets.models.context_state import ContextState


def _whole(value: object) -> int | None:
    """A JSON integer, or ``None``. Booleans are not numbers here."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ratio(value: object) -> float | None:
    """A JSON number, or ``None``. Booleans are not numbers here."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _amount(value: object) -> Decimal | None:
    """A non-negative amount from the string the route sends, or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except InvalidOperation, ValueError:
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def context_state_from_service(payload: Mapping[str, Any]) -> ContextState:
    """The panel's context line, from the projection of the service's meter.

    ``percent`` of ``0.0`` is a real measurement — a cold session with a known
    window — and is painted ``0%``. Only a value the route did not send becomes
    ``None`` and paints ``?``; the two are different claims.

    One limitation travels here: `/api/context` projects ``total``, ``approximate``
    and ``requests``, not the ``unpriced``/``unmeasured`` counts. A partial total
    (the ``+`` mark the in-process path can show) therefore cannot be rebuilt, and
    is not invented. The route is the place to widen if that mark is wanted.
    """
    percentage = _ratio(payload.get('percent'))
    used = _whole(payload.get('used'))
    window = _whole(payload.get('window'))
    cost_view = payload.get('cost')
    cost_view = cost_view if isinstance(cost_view, Mapping) else {}
    total = _amount(cost_view.get('total'))
    requests = _whole(cost_view.get('requests')) or 0
    approximate = bool(cost_view.get('approximate'))
    cost = SessionCost(
        total=total,
        reported=0 if approximate else requests,
        estimated=requests if approximate else 0,
        unpriced=requests if total is None and requests else 0,
    )
    return ContextState(percent=percentage, used=used, window=window, cost=cost)
