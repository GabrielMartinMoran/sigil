"""The receipts helper of the context layer."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import ModelMessage

MAINTENANCE_KEY = 'sigil_maintenance'


def receipts(messages: Sequence[ModelMessage]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for message in messages:
        for row in (message.metadata or {}).get(MAINTENANCE_KEY, ()):
            found[row['id']] = row
    return list(found.values())
