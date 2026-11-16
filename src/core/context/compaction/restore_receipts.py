"""The restore_receipts helper of the context layer."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from pydantic_ai.messages import ModelMessage

from src.core.context.compaction.identity import part_key
from src.core.context.compaction.receipts import MAINTENANCE_KEY


def restore_receipts(messages: list[ModelMessage], rows: Sequence[dict[str, Any]]) -> None:
    """Restore application metadata lost when a library merges request envelopes."""
    for index, message in enumerate(messages):
        anchors = {part_key(message, part, offset) for offset, part in enumerate(message.parts)}
        owned = {row['id']: row for row in (message.metadata or {}).get(MAINTENANCE_KEY, ())}
        owned.update((row['id'], row) for row in rows if row['anchor'] in anchors)
        if owned:
            messages[index] = replace(
                message, metadata={**(message.metadata or {}), MAINTENANCE_KEY: list(owned.values())}
            )
