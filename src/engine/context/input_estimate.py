"""Estimate the next input without treating a provider measurement as a local count.

A measurement is reusable only for the same model and unchanged projected prefix.
Compaction or a model swap invalidates that baseline. Unknown/encrypted reasoning
therefore stays visible as uncertainty instead of being silently priced as zero.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

from pydantic_ai.messages import ModelMessage, ModelMessagesTypeAdapter, ModelResponse, ThinkingPart

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.events import RECORD_KEY
from src.engine.context.tokens import estimate_message_tokens, estimate_messages_tokens, last_response_index


@dataclass(frozen=True, slots=True)
class InputEstimate:
    total: int
    source: Literal['local', 'provider_plus_delta'] = 'local'
    opaque_reserve: int = 0


def history_fingerprint(messages: Sequence[ModelMessage]) -> str:
    """Digest only sent parts and roles, excluding usage and application metadata."""
    envelopes = [replace(message, metadata=None) for message in messages]
    rows = ModelMessagesTypeAdapter.dump_python(envelopes, mode='json')
    parts = [(row['kind'], part) for row in rows for part in row['parts']]
    serialized = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(serialized.encode('utf-8')).hexdigest()


def estimate_input(messages: Sequence[ModelMessage], budget: ContextBudget) -> InputEstimate:
    opaque = sum(
        max(0, (message.usage.output_tokens or 0) - estimate_message_tokens(message))
        for message in messages
        if isinstance(message, ModelResponse)
        and any(isinstance(part, ThinkingPart) and part.signature and not part.content for part in message.parts)
    )
    local = budget.fixed + estimate_messages_tokens(messages) + opaque
    index = last_response_index(messages)
    if index is None:
        return InputEstimate(local, opaque_reserve=opaque)
    response = messages[index]
    assert isinstance(response, ModelResponse)
    record = (response.metadata or {}).get(RECORD_KEY, {})
    if not isinstance(record, dict) or not isinstance(record.get('input_fingerprint'), str):
        return InputEstimate(local, opaque_reserve=opaque)
    if (
        not budget.model_name
        or response.model_name != budget.model_name
        or response.usage.input_tokens is None
        or response.usage.unmeasured_requests
        or record.get('input_fingerprint') != history_fingerprint(messages[:index])
    ):
        return InputEstimate(local, opaque_reserve=opaque)
    prior_fixed = int(record.get('fixed_tokens', 0))
    output = max(response.usage.output_tokens or 0, estimate_message_tokens(response))
    delta = estimate_messages_tokens(messages[index + 1 :])
    measured = response.usage.input_tokens - prior_fixed + budget.fixed + output + delta
    return InputEstimate(max(local, measured), 'provider_plus_delta', opaque_reserve=opaque)
