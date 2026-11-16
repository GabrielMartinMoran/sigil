"""Explicit, durable context compaction without modifying transcript parts."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal
from uuid import uuid4

from pydantic_ai.messages import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RunUsage

from src.core.context.compaction.clock import step_count
from src.core.context.compaction.compaction_event import POLICY_VERSION
from src.core.context.compaction.events import RECORD_KEY
from src.core.context.compaction.identity import part_key
from src.core.context.compaction.maintenance_attempt import MaintenanceAttempt
from src.core.context.compaction.manual_compaction_result import ManualCompactionResult
from src.core.context.compaction.summary import Summary
from src.core.context.compaction.summary_generator import generate_summary
from src.core.context.context_budget import ContextBudget
from src.core.context.history_fingerprint import history_fingerprint
from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy


async def compact(
    messages: list[ModelMessage],
    strategy: AppendOnlyStrategy,
    budget: ContextBudget,
    *,
    mode: Literal['deterministic', 'llm'],
    model: Model | None = None,
    model_settings: ModelSettings | None = None,
) -> ManualCompactionResult:
    """Record the selected projection on a private history snapshot supplied by the caller."""
    before = strategy.view(messages, replace(budget, window=None)).estimated_tokens
    view = strategy.view(messages, budget, manual_compaction=mode)
    if not messages or not messages[-1].parts:
        return ManualCompactionResult('nothing_to_compact', before, before)
    if mode == 'llm':
        request = view.summary_request
        if request is None:
            return ManualCompactionResult('nothing_to_compact', before, before)
        if model is None:
            raise ValueError('A model is required for LLM compaction')
        attempt = MaintenanceAttempt(messages, RunUsage(), model.model_name)
        try:
            text = await generate_summary(
                model,
                request.messages,
                previous=request.previous,
                budget=budget,
                model_settings=model_settings,
                on_start=attempt.start,
                on_response=attempt.observe,
            )
        except asyncio.CancelledError:
            attempt.finish('cancelled', error_type='CancelledError')
            raise
        except Exception as error:
            attempt.finish('failed', error_type=type(error).__name__)
            raise
        attempt.finish('complete')
        view = strategy.view(messages, budget, manual_compaction=mode, summary=Summary(text, request.boundary))
    if view.plan is None or view.estimated_tokens >= before:
        status = 'not_reduced' if mode == 'llm' else 'nothing_to_compact'
        return ManualCompactionResult(status, before, before)
    current = messages[-1]
    from src.core.context.context_view_capability import _last_instruction_fingerprint

    instruction_fingerprint = _last_instruction_fingerprint(messages)
    record = {
        'version': POLICY_VERSION,
        'state': replace(view.plan.state, evaluated_steps=step_count(messages)).as_row(),
        'events': [event.as_row() for event in view.plan.events],
        'effective_policy': dict(view.plan.effective_policy),
        'input_fingerprint': history_fingerprint(view.messages),
        'instruction_fingerprint': instruction_fingerprint,
        'fixed_tokens': budget.fixed,
        'summary_error': None,
        'window': budget.window,
        'status': 'prepared',
        'request_id': f'manual:{uuid4().hex}',
        'anchor': part_key(current, current.parts[-1], len(current.parts) - 1),
    }
    messages[-1] = replace(current, metadata={**(current.metadata or {}), RECORD_KEY: record})
    return ManualCompactionResult('complete', before, view.estimated_tokens)
