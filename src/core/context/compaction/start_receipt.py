"""The start_receipt helper of the context layer."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import uuid4

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse
from pydantic_ai.usage import RequestUsage

from src.core.context.compaction.identity import part_key
from src.core.context.compaction.restore_receipts import restore_receipts


def start_receipt(messages: list[ModelMessage], model_name: str) -> str:
    message = messages[-1]
    anchor = part_key(message, message.parts[-1], len(message.parts) - 1)
    identifier = uuid4().hex
    response = ModelResponse(parts=[], model_name=model_name, usage=RequestUsage(unmeasured_requests=1))
    row = {'id': identifier, 'anchor': anchor, 'status': 'pending', 'response': _response_row(response)}
    restore_receipts(messages, [row])
    return identifier


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
