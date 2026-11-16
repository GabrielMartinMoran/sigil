"""The complete_receipt helper of the context layer."""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelResponse

from src.core.context.compaction.receipts import receipts
from src.core.context.compaction.restore_receipts import restore_receipts
from src.core.context.compaction.start_receipt import _response_row


def complete_receipt(
    messages: list[ModelMessage],
    identifier: str,
    *,
    status: str,
    response: ModelResponse | None = None,
    error_type: str | None = None,
) -> None:
    rows = receipts(messages)
    for row in rows:
        if row['id'] == identifier:
            row = {**row, 'status': status, 'error_type': error_type}
            if response is not None:
                row['response'] = _response_row(response)
            restore_receipts(messages, [row])
            return
