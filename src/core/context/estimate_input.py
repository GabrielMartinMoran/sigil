"""The estimate_input helper of the context layer."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_ai.messages import ModelMessage, ModelResponse, ThinkingPart

from src.core.context.compaction.events import RECORD_KEY
from src.core.context.context_budget import ContextBudget
from src.core.context.history_fingerprint import history_fingerprint
from src.core.context.input_estimate import InputEstimate
from src.core.context.tokens import estimate_message_tokens, estimate_messages_tokens, last_response_index


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
