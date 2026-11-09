"""Compaction triggers remain explicit through persistence and display."""

from src.engine.context.budget import ContextBudget
from src.engine.context.compaction.events import CompactionEvent
from src.engine.context.compaction.projection import prepare
from src.ui.widgets.debug_view import _compaction_label
from tests.test_append_only_pipeline import conversation


def test_pressure_and_instruction_change_are_both_recorded() -> None:
    result = prepare(
        conversation(30),
        ContextBudget(window=1000, output_reserve=0),
        image_turns=3,
        image_percent=10,
        compact_opportunistically=True,
    )
    assert result.events[0].triggers == ('pressure', 'instruction change')
    assert CompactionEvent.from_row(result.events[0].as_row()).triggers == result.events[0].triggers
    assert _compaction_label(result.events[0]) == 'deterministic (pressure, opportunistic: instruction change)'


def test_legacy_event_is_displayed_as_unknown() -> None:
    event = CompactionEvent.from_row({'step': 1, 'reason': 'deterministic'})
    assert not event.triggers
    assert _compaction_label(event) == 'deterministic (trigger unknown)'
