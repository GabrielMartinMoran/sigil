from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage

from src.engine.context.budget import ContextBudget
from src.engine.context.meter.debug import build_debug_report
from src.engine.context.meter.debug_render import render_debug_report
from src.engine.context.meter.request import RequestSnapshot
from src.engine.context.meter.snapshot import ContextSnapshot


def test_debug_turns_keep_steering_in_its_run():
    from src.engine.context.meter.debug import debug_turns

    messages = [
        ModelRequest(parts=[UserPromptPart(content='start')], run_id='first'),
        ModelResponse(parts=[TextPart(content='a')], run_id='first', usage=RequestUsage(input_tokens=10)),
        ModelRequest(parts=[UserPromptPart(content='steering')], run_id='first'),
        ModelResponse(parts=[TextPart(content='b')], run_id='first', usage=RequestUsage(input_tokens=20)),
        ModelRequest(parts=[UserPromptPart(content='next')], run_id='second'),
        ModelResponse(parts=[TextPart(content='c')], run_id='second', usage=RequestUsage(input_tokens=30)),
    ]
    first, second = debug_turns(messages)
    assert first.input_total == 30
    assert second.input_total == 30


def test_debug_turns_use_reported_input_and_cache_without_exposing_content():
    messages = [
        ModelRequest(parts=[UserPromptPart(content='secret prompt')]),
        ModelResponse(
            parts=[TextPart(content='secret answer')], usage=RequestUsage(input_tokens=100, cache_read_tokens=60)
        ),
        ModelResponse(parts=[TextPart(content='more')], usage=RequestUsage(input_tokens=200, cache_read_tokens=100)),
        ModelRequest(parts=[UserPromptPart(content='second secret')]),
        ModelResponse(parts=[TextPart(content='no usage')]),
    ]
    report = build_debug_report(
        messages,
        snapshot=ContextSnapshot(measured=None, projected=None, window=None, budget=ContextBudget(window=None)),
        request=RequestSnapshot(),
    )
    first, second = report.turns
    assert (first.last_input, first.cache_read, first.input_total, first.unmeasured) == (200, 160, 300, 0)
    assert first.cache_ratio == 160 / 300
    assert second.last_input is None and second.unmeasured == 1
    text = render_debug_report(report)
    assert 'Turns' in text and '160' in text
    assert 'secret' not in text
