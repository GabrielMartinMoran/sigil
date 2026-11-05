"""Tests for the adaptive base prompt and the tool gate.

A prompt that describes tools the model cannot see teaches it to plan around
capabilities it does not have, and the plan fails at the first call. Both halves of
that — what the prompt says and which definitions travel — are decided from the same
set of available features.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext, ToolDefinition

from src.engine.agents.model_features import IMAGE_INPUT, ModelFeatureGate
from src.engine.agents.prompts.render import render

# --- the renderer ----------------------------------------------------------

TEMPLATE = """Intro.

<!-- if: image_input -->
## Images

Pictures travel here.
<!-- endif -->

## Tools

- plain bullet
- **`read_file`** — read. <!-- if: read_file -->
- **`view_image`** — images. <!-- if: view_image -->

End.
"""


def test_a_block_is_kept_when_its_feature_is_available():
    rendered = render(TEMPLATE, {'image_input'})

    assert '## Images' in rendered


def test_a_block_is_dropped_when_its_feature_is_missing():
    rendered = render(TEMPLATE, set())

    assert '## Images' not in rendered
    assert 'Pictures travel here.' not in rendered


def test_a_line_is_dropped_with_its_marker():
    rendered = render(TEMPLATE, {'read_file'})

    assert 'read_file' in rendered
    assert 'view_image' not in rendered


def test_markers_never_reach_the_model():
    rendered = render(TEMPLATE, {'image_input', 'read_file', 'view_image'})

    assert '<!--' not in rendered
    assert '-->' not in rendered


def test_kept_sections_do_not_leave_holes():
    """Dropping a section must not leave three blank lines where it was."""
    rendered = render(TEMPLATE, set())

    assert '\n\n\n' not in rendered
    assert rendered.startswith('Intro.')
    assert rendered.endswith('End.\n')


def test_none_keeps_everything():
    """An embedded agent or a test wants the whole prompt."""
    rendered = render(TEMPLATE, None)

    assert '## Images' in rendered
    assert 'view_image' in rendered
    assert '<!--' not in rendered


def test_an_unclosed_block_is_an_error():
    with pytest.raises(ValueError) as error:
        render('A\n<!-- if: x -->\nB\n', set())
    assert 'never closed' in str(error.value)


def test_a_close_without_open_is_an_error():
    with pytest.raises(ValueError) as error:
        render('A\n<!-- endif -->\nB\n', set())
    assert 'without' in str(error.value)


def test_a_block_can_require_more_than_one_feature():
    rendered = render('<!-- if: a b -->\nboth\n<!-- endif -->\n', {'a'})

    assert 'both' not in rendered


def test_the_real_prompt_renders_without_markers():
    """The shipped template is valid, whatever the feature set."""
    from src.engine.agents.agent import _load_prompt

    template = _load_prompt()

    for features in (None, set(), {'image_input'}, {'run', 'read_file', 'view_image', 'image_input'}):
        rendered = render(template, features)
        assert '<!--' not in rendered
        assert rendered.strip()


def test_the_real_prompt_hides_images_when_the_model_cannot_read_them():
    from src.engine.agents.agent import _load_prompt

    template = _load_prompt()

    with_images = render(template, {'image_input', 'view_image'})
    without = render(template, {'read_file'})

    assert 'Images in the prompt' in with_images
    assert 'Images in the prompt' not in without
    assert 'view_image' not in without


# --- the gate --------------------------------------------------------------


def _definition(name: str, *, requires: tuple[str, ...] = ()) -> ToolDefinition:
    from src.engine.tooling.registrar import REQUIRES_METADATA_KEY

    metadata = {REQUIRES_METADATA_KEY: list(requires)} if requires else None
    return ToolDefinition(name=name, parameters_json_schema={'type': 'object'}, metadata=metadata)


def _run(capability: ModelFeatureGate, tools: list[ToolDefinition]) -> list[ToolDefinition]:
    import asyncio

    ctx = RunContext(deps=None, model=TestModel(), usage=None)
    return asyncio.run(capability.prepare_tools(ctx, tools))


def test_a_tool_without_requirements_is_always_offered():
    gate = ModelFeatureGate(supports=lambda feature: False)

    kept = _run(gate, [_definition('read_file')])

    assert [tool.name for tool in kept] == ['read_file']


def test_a_tool_whose_feature_is_missing_is_hidden():
    gate = ModelFeatureGate(supports=lambda feature: feature != IMAGE_INPUT)

    kept = _run(gate, [_definition('read_file'), _definition('view_image', requires=(IMAGE_INPUT,))])

    assert [tool.name for tool in kept] == ['read_file']


def test_a_tool_whose_feature_is_present_is_offered():
    gate = ModelFeatureGate(supports=lambda feature: True)

    kept = _run(gate, [_definition('view_image', requires=(IMAGE_INPUT,))])

    assert [tool.name for tool in kept] == ['view_image']


def test_a_call_to_a_hidden_tool_is_refused():
    """Defence in depth: a call that survived from another step does not run."""
    import asyncio

    from pydantic_ai.exceptions import ToolFailed

    gate = ModelFeatureGate(supports=lambda feature: feature != IMAGE_INPUT)
    ctx = RunContext(deps=None, model=TestModel(), usage=None)
    call = ToolCallPart(tool_name='view_image', args={'source': 'img_aaaaaaaa'})

    with pytest.raises(ToolFailed):
        asyncio.run(
            gate.before_tool_execute(
                ctx, call=call, tool_def=_definition('view_image', requires=(IMAGE_INPUT,)), args={}
            )
        )


def test_the_gate_reads_the_model_per_request():
    """`/model` swaps the route mid-session: the answer cannot be cached."""
    state = {'images': False}
    gate = ModelFeatureGate(supports=lambda feature: state['images'])
    tools = [_definition('view_image', requires=(IMAGE_INPUT,))]

    assert _run(gate, tools) == []

    state['images'] = True

    assert [tool.name for tool in _run(gate, tools)] == ['view_image']
