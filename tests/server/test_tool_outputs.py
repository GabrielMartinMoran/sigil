"""Human tool presentation must survive model serialization and session reloads."""

from __future__ import annotations


def test_should_publish_formatted_live_output_not_the_raw_logging_preview() -> None:
    from types import SimpleNamespace

    from src.core.tooling.tool_call_event import ToolCallEvent
    from src.core.tooling.tool_summary import ToolSummary
    from src.server.event_hub import EventHub
    from src.server.session_runtime import SessionRuntime

    hub = EventHub()
    runtime = SimpleNamespace(hub=hub, _publish=hub.publish)
    SessionRuntime._on_tool_event(
        runtime,
        ToolCallEvent(
            'run',
            'success',
            result_preview='[RunToolOutput(returncode=0)]',
            output='9 passed',
            summary=ToolSummary('9 passed', 'good'),
        ),
    )
    event = runtime.hub.replay()[-1]
    assert event.data['preview'] == '9 passed'
    assert 'returncode' not in event.data['preview']
