"""The turns table: cache, context and compaction events per turn, no content."""

from __future__ import annotations

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage

from src.core.context.compaction.events import CompactionEvent, CompactionReason
from src.core.context.context_budget import ContextBudget
from src.core.context.meter.debug import build_debug_report
from src.core.context.meter.debug_render import render_debug_report
from src.core.context.meter.request import RequestSnapshot
from src.core.context.meter.snapshot import ContextSnapshot

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


def test_the_turns_table_shows_context_and_cache_per_turn() -> None:
    report = _report(_turns())
    assert [turn.context_used for turn in report.turns] == [300, 400]
    assert [turn.cache_read for turn in report.turns] == [240, 100]
    assert report.turns[0].cache_ratio == 240 / 400


def test_each_event_is_attributed_to_the_turn_that_contains_its_step() -> None:
    events = (
        CompactionEvent(step=1, reason=CompactionReason.DETERMINISTIC),
        CompactionEvent(step=3, reason=CompactionReason.EMERGENCY),
    )
    report = _report(_turns(), events)
    assert [event.step for event in report.turns[0].events] == [1]
    assert [event.step for event in report.turns[1].events] == [3]


def test_the_plain_text_debug_names_the_events_and_never_the_conversation() -> None:
    report = _report(_turns(), (CompactionEvent(step=2, reason=CompactionReason.DETERMINISTIC),))
    text = render_debug_report(report)
    assert 'Turns' in text
    assert 'step 2 deterministic' in text
    assert 'secret' not in text
