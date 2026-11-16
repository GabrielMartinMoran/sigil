"""Maintenance records survive real agent hooks and the transactional session store."""

from __future__ import annotations

from src.core.context.meter.context_meter import ContextMeter
from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy
from tests.support.context_compaction_records import (
    history,
    pressured_window,
    run_recorded,
)


def test_debug_timeline_shows_real_compaction_on_request_that_recorded_it() -> None:
    from src.core.context.meter.debug import build_debug_report
    from src.core.context.meter.request_snapshot import RequestSnapshot
    from src.ui.widgets.debug_body import _turn_timeline

    messages = history(30)
    window = pressured_window(messages)
    recorded = run_recorded(messages, window=window)
    meter = ContextMeter(window=window, output_reserve=0)
    view = AppendOnlyStrategy().view(recorded, meter.budget())
    report = build_debug_report(
        recorded, snapshot=meter.snapshot(recorded), request=RequestSnapshot(), view_result=view
    )
    assert report.turns[-1].events
    timeline = _turn_timeline(report).plain
    assert 'Step 1' in timeline
    assert 'compaction: deterministic' in timeline
