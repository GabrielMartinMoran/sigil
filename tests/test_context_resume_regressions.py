"""Damaged derived records and cross-model media do not poison a conversation."""

import io

from PIL import Image
from pydantic_ai.messages import BinaryImage, ModelRequest, ModelResponse, TextPart, UserPromptPart

from src.engine.context.capability import compatible_model_history
from src.engine.context.compaction.events import POLICY_VERSION, RECORD_KEY, CompactionState
from src.engine.context.compaction.records import recorded_state


def test_invalid_frontier_is_ignored_without_replaying_its_summary() -> None:
    record = {
        'version': POLICY_VERSION,
        'state': CompactionState(evaluated_steps=9, summary='discarded future').as_row(),
        'events': [],
        'request_id': 'old:9',
    }
    messages = [ModelResponse(parts=[TextPart('kept')], metadata={RECORD_KEY: record})]

    state, events = recorded_state(messages)

    assert state == CompactionState()
    assert events == ()
    assert messages[0].metadata[RECORD_KEY] == record


def test_cross_model_history_normalizes_nonportable_images_without_editing_original() -> None:
    buffer = io.BytesIO()
    Image.new('RGB', (2, 2)).save(buffer, format='BMP')
    image = BinaryImage(data=buffer.getvalue(), media_type='image/bmp', identifier='img_test')
    original = [ModelRequest(parts=[UserPromptPart(['look', image])])]

    projected = compatible_model_history(original, 'different-model')
    converted = projected[0].parts[0].content[1]

    assert converted.media_type == 'image/png'
    assert converted.data.startswith(b'\x89PNG\r\n\x1a\n')
    assert converted.identifier == image.identifier
    assert original[0].parts[0].content[1] is image
    assert image.media_type == 'image/bmp'


def test_prepared_record_uses_the_durable_transcript_clock() -> None:
    from types import SimpleNamespace

    from src.engine.context.budget import ContextBudget
    from src.engine.context.capability import ContextViewCapability
    from src.engine.context.compaction.coordinator import CompactionPlan
    from src.engine.context.meter.context_meter import ContextMeter
    from src.engine.context.strategy.append_only import AppendOnlyStrategy
    from src.engine.context.strategy.view_result import ViewResult

    messages = [ModelResponse(parts=[TextPart('answer')]), ModelRequest(parts=[UserPromptPart('continue')])]
    ctx = SimpleNamespace(messages=messages, run_id='run', run_step=1)
    capability = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter(window=100_000))
    view = ViewResult(messages, 10, plan=CompactionPlan(state=CompactionState(evaluated_steps=4)))

    capability._record_decision(ctx, view, ContextBudget(window=100_000), 'instructions')

    record = messages[-1].metadata[RECORD_KEY]
    assert record['state']['evaluated_steps'] == 1
    assert recorded_state(messages)[0].evaluated_steps == 1


def test_switching_models_does_not_reuse_previous_response_state() -> None:
    from pydantic_ai.messages import ThinkingPart

    response = ModelResponse(
        parts=[ThinkingPart('', signature='encrypted', provider_name='openai'), TextPart('answer', id='msg_old')],
        model_name='terra',
        provider_name='openai',
        provider_response_id='resp_old',
        history_digest='old-digest',
    )

    projected = compatible_model_history([response], 'sol')

    assert projected[0].provider_response_id is None
    assert projected[0].history_digest is None
    assert projected[0].parts[0].content == 'answer'
    assert projected[0].parts[0].id is None
    assert response.provider_response_id == 'resp_old'
    assert len(response.parts) == 2


def test_tool_images_are_normalized_and_portable_images_are_unchanged() -> None:
    from pydantic_ai.messages import ToolReturnPart

    buffer = io.BytesIO()
    Image.new('RGB', (2, 2)).save(buffer, format='TIFF')
    image = BinaryImage(data=buffer.getvalue(), media_type='image/tiff', identifier='img_tool')
    tool = ModelRequest(parts=[ToolReturnPart('image_tool', [image], 'call')])
    converted = compatible_model_history([tool], 'sol')[0].parts[0].content[0]
    assert converted.media_type == 'image/png'
    assert converted.identifier == 'img_tool'
    portable = ModelRequest(parts=[UserPromptPart([converted])])
    assert compatible_model_history([portable], 'sol')[0] is portable


def test_invalid_preparation_does_not_hide_a_valid_response_with_the_same_request_id() -> None:
    record = {
        'version': POLICY_VERSION,
        'state': CompactionState(evaluated_steps=1, compacted_calls=('kept',)).as_row(),
        'events': [],
        'request_id': 'same-request',
    }
    messages = [
        ModelRequest(parts=[UserPromptPart('go')], metadata={RECORD_KEY: record}),
        ModelResponse(parts=[TextPart('answer')], metadata={RECORD_KEY: record}),
    ]

    state, _ = recorded_state(messages)

    assert state.compacted_calls == ('kept',)
