"""The turns table: cache, context and compaction events per turn, no content."""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage
from rich.console import Console

from src.core.context.compaction.compaction_event import CompactionEvent
from src.core.context.compaction.compaction_reason import CompactionReason
from src.core.context.context_budget import ContextBudget
from src.core.context.meter.context_snapshot import ContextSnapshot
from src.core.context.meter.debug import build_debug_report
from src.core.context.meter.request_snapshot import RequestSnapshot
from src.ui.widgets.panels.debug_body import build_debug_body

_EMPTY = ContextSnapshot(measured=None, projected=None, window=None, budget=ContextBudget(window=None))


def _report(messages, events=()):
    return build_debug_report(
        messages,
        snapshot=_EMPTY,
        request=RequestSnapshot(),
        events=events,
    )


def _turns():
    return [
        ModelRequest(parts=[UserPromptPart(content='first secret')], run_id='a'),
        ModelResponse(
            parts=[TextPart(content='a1')], run_id='a', usage=RequestUsage(input_tokens=100, cache_read_tokens=40)
        ),
        ModelResponse(
            parts=[TextPart(content='a2')], run_id='a', usage=RequestUsage(input_tokens=300, cache_read_tokens=200)
        ),
        ModelRequest(parts=[UserPromptPart(content='second secret')], run_id='b'),
        ModelResponse(
            parts=[TextPart(content='b1')], run_id='b', usage=RequestUsage(input_tokens=400, cache_read_tokens=100)
        ),
    ]


def test_the_tui_table_names_the_events_and_never_the_conversation() -> None:
    report = _report(_turns(), (CompactionEvent(step=2, reason=CompactionReason.EMERGENCY),))
    console = Console(width=200, record=True, force_terminal=False)
    console.print(build_debug_body(report))
    rendered = console.export_text()
    assert 'Compaction events' in rendered
    assert 'step 2 emergency' in rendered
    assert 'secret' not in rendered


def test_timeline_distinguishes_estimated_from_provider_reported_step_costs() -> None:
    from decimal import Decimal

    from src.ui.widgets.panels.debug_body import _turn_timeline

    messages = [
        ModelRequest(parts=[UserPromptPart(content='go')], run_id='a'),
        ModelResponse(
            parts=[TextPart('first')], run_id='a', usage=RequestUsage(input_tokens=100, cost=Decimal('0.20'))
        ),
        ModelResponse(
            parts=[TextPart('second')],
            run_id='a',
            usage=RequestUsage(input_tokens=100),
            provider_details={'cost': 0.0123},
        ),
        ModelResponse(parts=[TextPart('third')], run_id='a', provider_details={'cost': 0}),
    ]
    text = _turn_timeline(_report(messages)).plain
    assert 'Step 3' in text and 'cost $0' in text
    assert 'Step 2' in text and 'cost $0.0123' in text
    assert 'cost ~$0.20' in text
