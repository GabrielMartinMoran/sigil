"""Tests for `view_image`: the pixels come back as pixels, not as a path.

Returning a `BinaryImage` from a tool is not the same as returning a link to it:
pydantic-ai maps multimodal tool results natively, so the model really does see the
image. That is the whole point of dropping the old ones.
"""

from __future__ import annotations

import asyncio
import inspect
import io
from typing import Any

from PIL import Image
from pydantic_ai import ToolReturn
from pydantic_ai.messages import BinaryImage, ModelRequest, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext

from src.engine.attachments.tools import register_image_tools
from src.engine.files.models.file_tool_error import FileToolError

# --- helpers ---------------------------------------------------------------


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (64, 64), color='#3465a4').save(buffer, format='PNG')
    return buffer.getvalue()


def _image(identifier: str) -> BinaryImage:
    return BinaryImage(data=_png(), media_type='image/png', identifier=identifier)


def _call(history: list[Any], image_id: str) -> Any:
    """Registers the tool on a throwaway agent and calls it with a context."""
    from pydantic_ai import Agent

    agent = Agent(TestModel())
    register_image_tools(agent)

    ctx = RunContext(deps=None, model=TestModel(), usage=None)
    ctx.messages[:] = list(history)

    # The tool is registered on the agent; fetch its function and call it directly,
    # which is what the graph does with the run's context.
    tool = next(t for t in agent._function_toolset.tools.values() if t.name == 'view_image')
    result = tool.function(ctx, image_id=image_id)
    return asyncio.run(result) if inspect.isawaitable(result) else result


# --- the happy path --------------------------------------------------------


def test_the_image_comes_back_as_an_image():
    history = [ModelRequest(parts=[UserPromptPart(content=['mira', _image('img_aaaaaaaa')])])]

    result = _call(history, 'img_aaaaaaaa')

    assert isinstance(result, ToolReturn)
    only = next(iter(result.content))
    assert isinstance(only, BinaryImage)
    assert only.identifier == 'img_aaaaaaaa'
    assert only.is_image


def test_the_answer_is_a_short_line_and_the_pixels_travel_beside_it():
    """`return_value` is what the model reads as the result; `content` is the image."""
    history = [ModelRequest(parts=[UserPromptPart(content=['mira', _image('img_aaaaaaaa')])])]

    result = _call(history, 'img_aaaaaaaa')

    assert isinstance(result.return_value, str)
    assert 'img_aaaaaaaa' in result.return_value
    assert len(result.return_value) < 100


def test_the_bytes_are_the_original_ones():
    image = _image('img_aaaaaaaa')
    history = [ModelRequest(parts=[UserPromptPart(content=['mira', image])])]

    result = _call(history, 'img_aaaaaaaa')

    only = next(iter(result.content))
    assert only.data == image.data


# --- the failure path ------------------------------------------------------


def test_an_unknown_id_raises_a_tool_error():
    """A missing id is a mistake to correct, not a silent empty answer."""
    history = [ModelRequest(parts=[UserPromptPart(content=['mira', _image('img_aaaaaaaa')])])]

    try:
        _call(history, 'img_deadbeef')
    except FileToolError as error:
        assert 'img_deadbeef' in str(error)
    else:  # pragma: no cover - the assertion below is the point
        raise AssertionError('expected FileToolError')


def test_an_empty_history_says_there_are_no_ids():
    try:
        _call([], 'img_deadbeef')
    except FileToolError as error:
        assert 'session' in str(error)
    else:  # pragma: no cover
        raise AssertionError('expected FileToolError')
