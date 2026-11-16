"""The active_provider helper of the core layer."""

from __future__ import annotations

from typing import Any


def active_provider(model_service: Any) -> str | None:
    """`system` of the model the next request goes to, or ``None`` if unknown.

    It is the same string a `ThinkingPart` records in `provider_name`, which is what
    decides whether its signature can be sent back. ``None`` disables the check
    rather than guessing: a wrong guess drops reasoning that was fine.
    """
    try:
        model = model_service.current_model()
    except Exception:  # noqa: BLE001 - the router can be unconfigured
        return None
    return getattr(model, 'system', None)
