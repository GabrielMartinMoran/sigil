"""Operational tables distinguish recorded history, projection and unknown sizes."""

from __future__ import annotations

from dataclasses import replace

from src.core.context.meter.context_meter import ContextMeter
from src.core.context.meter.debug import build_debug_report
from src.core.context.meter.debug_render import render_debug_report
from src.core.context.meter.request import RequestSnapshot
from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy
from tests.support.context_compaction_records import history


def report(messages: list, *, window: int = 100000):
    meter = ContextMeter(window=window, output_reserve=0)
    view = AppendOnlyStrategy().view(messages, meter.budget())
    return build_debug_report(messages, snapshot=meter.snapshot(messages), request=RequestSnapshot(), view_result=view)


def test_skill_metrics_render_in_text_and_debug_view() -> None:
    from src.ui.widgets.debug_body import build_debug_body

    metrics = {
        'instructions_tokens': 100,
        'resources_tokens': 20,
        'resources': 3,
        'partial': 1,
        'binary': 2,
        'unavailable': 3,
        'unavailable_skills': 4,
    }
    debug_report = replace(report(history(20)), request=RequestSnapshot(captured=True, skills=metrics))

    text = render_debug_report(debug_report)
    assert 'instructions ~100 tokens; resources ~20 tokens; resources 3; partial 1' in text
    assert 'binary 2; unavailable 3; unavailable skills 4' in text
    assert build_debug_body(debug_report) is not None
