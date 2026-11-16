"""The debug contract retains measurements, including immutable maintenance events."""

from dataclasses import replace

from src.core.context.compaction.compaction_event import CompactionEvent
from src.core.context.meter.context_meter import ContextMeter
from src.core.context.meter.debug import build_debug_report
from src.core.context.meter.debug_render import render_debug_report
from src.core.context.meter.debug_snapshot import DEBUG_REPORT_ADAPTER
from src.core.context.meter.debug_turn import DebugTurn
from src.core.context.meter.request_snapshot import RequestSnapshot
from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy


def test_should_roundtrip_recorded_and_proposed_compaction_with_immutable_maps() -> None:
    event = CompactionEvent(step=1, detail={'tool_results': 2}, policy={'pinned_tools': ['run']})
    meter = ContextMeter(window=100_000)
    report = build_debug_report(
        [],
        snapshot=meter.snapshot([]),
        request=RequestSnapshot(),
        view_result=AppendOnlyStrategy().view([], meter.budget()),
    )
    assert report.view_result is not None
    report = replace(
        report,
        turns=(
            DebugTurn(
                index=1,
                last_input=10,
                input_total=10,
                cache_read=0,
                unmeasured=0,
                measured=1,
                cache_ratio=0,
                events=(event,),
                step_events=((event,),),
            ),
        ),
        view_result=replace(report.view_result, events=(event, event), recorded_events=1),
    )
    decoded = DEBUG_REPORT_ADAPTER.validate_json(DEBUG_REPORT_ADAPTER.dump_json(report))
    assert decoded.turns == report.turns
    assert decoded.view_result.events == report.view_result.events
    assert DEBUG_REPORT_ADAPTER.dump_json(decoded) == DEBUG_REPORT_ADAPTER.dump_json(report)
    assert render_debug_report(decoded) == render_debug_report(report)
    assert 'recorded' in render_debug_report(decoded)
    assert 'next request projection' in render_debug_report(decoded)
