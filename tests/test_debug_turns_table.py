"""The turns table: cache, context and compaction events per turn, no content."""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage
from rich.console import Console

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.events import CompactionEvent, CompactionReason
from src.engine.context.meter.debug import build_debug_report
from src.engine.context.meter.debug_render import render_debug_report
from src.engine.context.meter.request import RequestSnapshot
from src.engine.context.meter.snapshot import ContextSnapshot
from src.ui.widgets.debug_view import build_debug_body

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


def test_the_turns_table_shows_context_and_cache_per_turn():
    report = _report(_turns())
    assert [turn.context_used for turn in report.turns] == [300, 400]
    assert [turn.cache_read for turn in report.turns] == [240, 100]
    assert report.turns[0].cache_ratio == 240 / 400


def test_each_event_is_attributed_to_the_turn_that_contains_its_step():
    events = (
        CompactionEvent(step=1, reason=CompactionReason.OPERATIONAL),
        CompactionEvent(step=3, reason=CompactionReason.BUDGET),
    )
    report = _report(_turns(), events)
    assert [event.step for event in report.turns[0].events] == [1]
    assert [event.step for event in report.turns[1].events] == [3]


def test_the_plain_text_debug_names_the_events_and_never_the_conversation():
    report = _report(_turns(), (CompactionEvent(step=2, reason=CompactionReason.OPERATIONAL),))
    text = render_debug_report(report)
    assert 'Turns' in text
    assert 'step 2 operational' in text
    assert 'secret' not in text


def test_the_tui_table_names_the_events_and_never_the_conversation():
    report = _report(_turns(), (CompactionEvent(step=2, reason=CompactionReason.EMERGENCY),))
    console = Console(width=200, record=True, force_terminal=False)
    console.print(build_debug_body(report))
    rendered = console.export_text()
    assert 'Compaction events' in rendered
    assert 'step 2 emergency' in rendered
    assert 'secret' not in rendered
