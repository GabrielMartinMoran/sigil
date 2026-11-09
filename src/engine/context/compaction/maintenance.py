"""Content-free receipts for auxiliary requests, owned by transcript envelopes.

A receipt is written before the network call and completed before accepting its
checkpoint. Failures and cancellations therefore cannot turn an attempted request
into a free one. Stable ids deduplicate copied envelopes; rollback follows the owner.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from typing import Any
from uuid import uuid4

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.usage import RequestUsage, RunUsage

from src.engine.context.compaction.identity import part_key

MAINTENANCE_KEY = 'sigil_maintenance'


def receipts(messages: Sequence[ModelMessage]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for message in messages:
        for row in (message.metadata or {}).get(MAINTENANCE_KEY, ()):
            found[row['id']] = row
    return list(found.values())


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


def start_receipt(messages: list[ModelMessage], model_name: str) -> str:
    message = messages[-1]
    anchor = part_key(message, message.parts[-1], len(message.parts) - 1)
    identifier = uuid4().hex
    response = ModelResponse(parts=[], model_name=model_name, usage=RequestUsage(unmeasured_requests=1))
    row = {'id': identifier, 'anchor': anchor, 'status': 'pending', 'response': _response_row(response)}
    restore_receipts(messages, [row])
    return identifier


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


def _response_row(response: ModelResponse) -> dict[str, Any]:
    details = response.provider_details or {}
    safe = replace(
        response,
        parts=[],
        metadata=None,
        failed_attempts=None,
        provider_details={'cost': details['cost']} if 'cost' in details else None,
    )
    return ModelMessagesTypeAdapter.dump_python([safe], mode='json')[0]


def maintenance_responses(messages: Sequence[ModelMessage]) -> Iterator[ModelResponse]:
    """Bill auxiliary calls without adding fake conversational steps to the history."""
    for row in receipts(messages):
        response = ModelMessagesTypeAdapter.validate_python([row['response']])[0]
        if isinstance(response, ModelResponse):
            yield response


@dataclass
class MaintenanceAttempt:
    """A run-local auxiliary request; no state is shared between agents."""

    messages: list[ModelMessage]
    usage: RunUsage
    model_name: str
    identifier: str | None = None

    def start(self) -> None:
        self.identifier = start_receipt(self.messages, self.model_name)
        self.usage.requests += 1

    def observe(self, response: ModelResponse) -> None:
        self.usage.incr(response.usage)
        if self.identifier is not None:
            complete_receipt(self.messages, self.identifier, status='responded', response=response)

    def finish(self, status: str, *, error_type: str | None = None) -> None:
        if self.identifier is not None:
            complete_receipt(self.messages, self.identifier, status=status, error_type=error_type)


def last_summary_failure(messages: Sequence[ModelMessage]) -> str | None:
    rows = receipts(messages)
    if not rows or rows[-1]['status'] not in ('failed', 'cancelled'):
        return None
    return rows[-1].get('error_type')
