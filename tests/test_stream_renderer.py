"""Tests del render incremental de eventos del modelo."""

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
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta='Hola')),
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=' mundo')),
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta='!')),
        ]
    )
    assert output == 'Hola mundo!\n'


def test_streams_thinking_before_text():
    output = _render(
        [
            PartStartEvent(index=0, part=ThinkingPart(content='Pensando')),
            PartDeltaEvent(index=0, delta=ThinkingPartDelta(content_delta='...')),
            PartStartEvent(index=1, part=TextPart(content='Respuesta')),
            PartDeltaEvent(index=1, delta=TextPartDelta(content_delta='!')),
        ]
    )
    assert 'Pensando...' in output
    assert 'Respuesta!' in output
    # El pensamiento y el texto quedan en líneas separadas.
    assert output.index('Pensando...') < output.index('Respuesta!')
    assert 'Pensando...' in output.splitlines()[0]


def test_new_model_response_starts_on_its_own_line():
    # Un mensaje intermedio con texto + tool call seguido de la respuesta final.
    output = _render(
        [
            PartStartEvent(index=0, part=TextPart(content='Voy a revisar')),
            PartStartEvent(index=1, part=ToolCallPart(tool_name='run', args={}, tool_call_id='c1')),
            PartStartEvent(index=0, part=TextPart(content='Listo')),
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=', terminé')),
        ]
    )
    assert output == 'Voy a revisar\nListo, terminé\n'


def test_empty_stream_prints_nothing():
    assert _render([]) == ''


def test_renderer_can_be_reused_between_turns():
    output = StringIO()
    console = Console(file=output, force_terminal=False, width=1000, color_system=None)
    renderer = StreamRenderer(console)

    renderer.render_events(_FakeEvents([PartStartEvent(index=0, part=TextPart(content='uno'))]))
    renderer.finish()
    renderer.render_events(_FakeEvents([PartStartEvent(index=0, part=TextPart(content='dos'))]))
    renderer.finish()

    assert output.getvalue() == 'uno\ndos\n'
