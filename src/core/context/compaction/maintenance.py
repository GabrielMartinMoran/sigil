"""Content-free receipts for auxiliary requests, owned by transcript envelopes.

A receipt is written before the network call and completed before accepting its
checkpoint. Failures and cancellations therefore cannot turn an attempted request
into a free one. Stable ids deduplicate copied envelopes; rollback follows the owner.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse

from src.core.context.compaction.receipts import receipts


def maintenance_responses(messages: Sequence[ModelMessage]) -> Iterator[ModelResponse]:
    """Bill auxiliary calls without adding fake conversational steps to the history."""
    for row in receipts(messages):
        response = ModelMessagesTypeAdapter.validate_python([row['response']])[0]
        if isinstance(response, ModelResponse):
            yield response


def last_summary_failure(messages: Sequence[ModelMessage]) -> str | None:
    rows = receipts(messages)
    if not rows or rows[-1]['status'] not in ('failed', 'cancelled'):
        return None
    return rows[-1].get('error_type')
