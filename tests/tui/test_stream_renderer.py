"""Tests for the incremental rendering of model events."""

from io import StringIO

from pydantic_ai.messages import (
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPart,
)
from rich.console import Console

from src.ui.stream_renderer import StreamRenderer


class _FakeEvents:
    def __init__(self, events):
        self._events = events

    async def __aiter__(self):
        for event in self._events:
            yield event


def _render(events):
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=1000, color_system=None)
    renderer = StreamRenderer(console)
    renderer.render_events(_FakeEvents(events))
    renderer.finish()
    return output.getvalue()


def test_streams_text_as_it_arrives_without_duplicates():
    output = _render(
        [
            PartStartEvent(index=0, part=TextPart(content='')),
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta='Hello')),
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=' world')),
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta='!')),
        ]
    )
    assert output == 'Hello world!\n'


def test_streams_thinking_before_text():
    output = _render(
        [
            PartStartEvent(index=0, part=ThinkingPart(content='Thinking')),
            PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta='...')),
            PartStartEvent(index=1, part=TextPart(content='Answer')),
            PartDeltaEvent(index=1, delta=TextPartDelta(content_delta='!')),
        ]
    )
    assert 'Thinking...' in output
    assert 'Answer!' in output
    # Thinking and text end up on separate lines.
    assert output.index('Thinking...') < output.index('Answer!')
    assert 'Thinking...' in output.splitlines()[0]


def test_new_model_response_starts_on_its_own_line():
    # An intermediate message with text + tool call followed by the final answer.
    output = _render(
        [
            PartStartEvent(index=0, part=TextPart(content='Let me check')),
            PartStartEvent(index=1, part=ToolCallPart(tool_name='run', args={}, tool_call_id='c1')),
            PartStartEvent(index=0, part=TextPart(content='Done')),
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=', finished')),
        ]
    )
    assert output == 'Let me check\nDone, finished\n'


def test_empty_stream_prints_nothing():
    assert _render([]) == ''


def test_renderer_can_be_reused_between_turns():
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=1000, color_system=None)
    renderer = StreamRenderer(console)

    renderer.render_events(_FakeEvents([PartStartEvent(index=0, part=TextPart(content='one'))]))
    renderer.finish()
    renderer.render_events(_FakeEvents([PartStartEvent(index=0, part=TextPart(content='two'))]))
    renderer.finish()

    assert output.getvalue() == 'one\ntwo\n'
