"""Tests for `view_image`: one field, three kinds of source, and no loops.

Returning a `BinaryImage` from a tool is not the same as returning a link to it:
pydantic-ai maps multimodal tool results natively, so the model really does see the
image. That is the whole point of dropping the old ones.
"""

from __future__ import annotations

import asyncio
import inspect
import io
from pathlib import Path
from typing import Any

import pytest
from PIL import Image
from pydantic_ai import ToolReturn
from pydantic_ai.messages import BinaryImage, ModelMessage, ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import RunContext

from src.engine.attachments.tools import image_id_for, is_url, register_image_tools
from src.engine.files.models.file_tool_error import FileToolError

# --- helpers ---------------------------------------------------------------


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (64, 64), color='#3465a4').save(buffer, format='PNG')
    return buffer.getvalue()


def _image(identifier: str) -> BinaryImage:
    return BinaryImage(data=_png(), media_type='image/png', identifier=identifier)


def _call(history: list[Any], source: str) -> Any:
    """Registers the tool on a throwaway agent and calls it with a context."""
    from pydantic_ai import Agent

    agent = Agent(TestModel())
    register_image_tools(agent)

    ctx = RunContext(deps=None, model=TestModel(), usage=None)
    ctx.messages[:] = list(history)

    tool = next(t for t in agent._function_toolset.tools.values() if t.name == 'view_image')
    result = tool.function(ctx, source=source)
    return asyncio.run(result) if inspect.isawaitable(result) else result


def _carrying(identifier: str, *, run: str = 'r1') -> list[ModelMessage]:
    return [ModelRequest(parts=[UserPromptPart(content=['mira', _image(identifier)])], run_id=run)]


def _older_round(identifier: str) -> list[ModelMessage]:
    """The picture arrived in a finished round: the case the tool exists for."""
    return [
        *_carrying(identifier, run='r1'),
        ModelResponse(parts=[], run_id='r1'),
        ModelRequest(parts=[UserPromptPart(content='otra cosa')], run_id='r2'),
    ]


# --- the kind of source ----------------------------------------------------


@pytest.mark.parametrize(
    'source,expected',
    [
        ('https://example.com/a.png', True),
        ('http://example.com/a.png', True),
        ('/tmp/a.png', False),
        ('img_ab12cd34', False),
        ('', False),
    ],
)
def test_a_url_is_told_apart_from_a_path(source, expected):
    assert is_url(source) is expected


def test_an_id_is_stable_for_the_same_source():
    """The same URL is the same picture: asking twice must not mint a second id."""
    assert image_id_for('https://example.com/a.png') == image_id_for('https://example.com/a.png')
    assert image_id_for('https://example.com/a.png') != image_id_for('https://example.com/b.png')


def test_an_id_looks_like_the_ones_the_cache_understands():
    assert image_id_for('/tmp/shot.png').startswith('img_')


# --- from the session ------------------------------------------------------


def test_an_image_from_the_session_comes_back_as_an_image():
    result = _call(_older_round('img_aaaaaaaa'), 'img_aaaaaaaa')

    assert isinstance(result, ToolReturn)
    only = next(iter(result.content))
    assert isinstance(only, BinaryImage)
    assert only.identifier == 'img_aaaaaaaa'
    assert only.is_image


def test_the_answer_is_a_short_line_and_the_pixels_travel_beside_it():
    result = _call(_older_round('img_aaaaaaaa'), 'img_aaaaaaaa')

    assert isinstance(result.return_value, str)
    assert 'img_aaaaaaaa' in result.return_value
    assert len(result.return_value) < 120


def test_the_picture_is_the_one_that_was_pasted():
    """The bytes are re-encoded on the way out; the picture has to be the same one."""
    image = _image('img_aaaaaaaa')
    history = [
        ModelRequest(parts=[UserPromptPart(content=['mira', image])], run_id='r1'),
        ModelRequest(parts=[UserPromptPart(content='charla')], run_id='r2'),
    ]
    result = _call(history, 'img_aaaaaaaa')

    returned = next(iter(result.content))
    original = Image.open(io.BytesIO(image.data))
    again = Image.open(io.BytesIO(returned.data))
    assert again.size == original.size
    assert returned.media_type.startswith('image/')


def test_an_unknown_id_raises_a_tool_error():
    with pytest.raises(FileToolError) as error:
        _call(_carrying('img_aaaaaaaa'), 'img_deadbeef')
    assert 'img_deadbeef' in str(error.value)


def test_an_empty_source_raises_a_tool_error():
    with pytest.raises(FileToolError):
        _call(_carrying('img_aaaaaaaa'), '   ')


# --- the loop guard --------------------------------------------------------


def test_a_picture_the_round_already_carries_is_not_sent_again():
    """The loop guard: asking twice in a round costs one sentence, not the pixels."""
    history = [
        *_carrying('img_aaaaaaaa'),
        ModelResponse(parts=[], run_id='r1'),
        ModelRequest(parts=[UserPromptPart(content='y ahora?')], run_id='r1'),
    ]

    result = _call(history, 'img_aaaaaaaa')

    assert isinstance(result, ToolReturn)
    assert result.content == [] or result.content is None
    assert 'already attached' in result.return_value


def test_a_picture_from_a_finished_round_is_sent_again():
    """A new round starts clean: the guard is per round, not forever."""
    history = [
        *_carrying('img_aaaaaaaa', run='r1'),
        ModelResponse(parts=[], run_id='r1'),
        ModelRequest(parts=[UserPromptPart(content='otra cosa')], run_id='r2'),
    ]

    result = _call(history, 'img_aaaaaaaa')

    assert next(iter(result.content)).identifier == 'img_aaaaaaaa'


# --- from a path -----------------------------------------------------------


def test_an_image_is_loaded_from_a_path(tmp_path):
    target = tmp_path / 'shot.png'
    target.write_bytes(_png())

    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        result = _call([], 'shot.png')
    finally:
        os.chdir(cwd)

    only = next(iter(result.content))
    assert isinstance(only, BinaryImage)
    assert only.identifier.startswith('img_')


def test_a_path_that_is_not_an_image_says_so(tmp_path):
    target = tmp_path / 'notes.txt'
    target.write_text('hola')

    with pytest.raises(FileToolError) as error:
        _call([], str(target))
    assert 'not an image' in str(error.value)


def test_a_path_outside_the_workspace_is_refused():
    with pytest.raises(FileToolError) as error:
        _call([], '/etc/hosts')
    assert 'outside the workspace' in str(error.value)


@pytest.mark.parametrize('source', ['img_aaaaaaaa', '[Image #1]', 'https://example.com/image.png'])
def test_should_skip_loading_and_decoding_an_attached_source(source: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.engine.attachments import tools

    identifier = image_id_for(source) if is_url(source) else source
    history = _carrying(identifier)

    def no_prepare(raw: bytes) -> Any:
        pytest.fail('An attached source must not be decoded again')

    async def no_download(*args: Any, **kwargs: Any) -> bytes:
        pytest.fail('An attached source must not be downloaded again')

    monkeypatch.setattr(tools, 'prepare_image', no_prepare)
    monkeypatch.setattr(tools, 'download_image', no_download)
    result = _call(history, f' {source} ')
    assert 'already attached' in result.return_value
    assert not result.content


def test_should_skip_path_io_for_an_attached_image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / 'shot.png'
    target.write_bytes(_png())
    history = _carrying(image_id_for(str(target.resolve())))

    def no_read(path: Path) -> bytes:
        pytest.fail('An attached image must not be read again')

    monkeypatch.setattr(Path, 'read_bytes', no_read)
    result = _call(history, str(target))
    assert 'already attached' in result.return_value


def test_should_check_path_authorization_even_for_an_attached_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.engine.files import paths

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    external = tmp_path / 'external.png'
    external.write_bytes(_png())
    link = workspace / 'shot.png'
    link.symlink_to(external)
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(paths, 'temp_dirs', lambda: ())
    history = _carrying(image_id_for(str(external.resolve())))
    with pytest.raises(FileToolError, match='outside the workspace'):
        _call(history, str(link))
