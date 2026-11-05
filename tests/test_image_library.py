"""Tests for the image library: ids that persist, and getting a picture back.

An image is the most expensive item in the history, so it cannot travel with every
request forever. What travels is its id; the bytes stay in the session log, and a
tool brings them back on demand.
"""

from __future__ import annotations

import io

from PIL import Image
from pydantic_ai.messages import BinaryImage, ModelRequest, UserPromptPart

from src.engine.attachments.image_attachment import new_image_id
from src.engine.attachments.library import find_image, image_ids, is_image_id

# --- helpers ---------------------------------------------------------------


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (64, 64), color='#3465a4').save(buffer, format='PNG')
    return buffer.getvalue()


def _image(identifier: str) -> BinaryImage:
    return BinaryImage(data=_png(), media_type='image/png', identifier=identifier)


def _prompt(*content: object) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=list(content))])


# --- ids -------------------------------------------------------------------


def test_a_minted_id_is_recognisable_and_short():
    image_id = new_image_id()

    assert is_image_id(image_id)
    assert len(image_id) <= 20


def test_minted_ids_do_not_repeat():
    ids = {new_image_id() for _ in range(500)}

    assert len(ids) == 500


def test_the_marker_of_a_prompt_is_not_an_id():
    """`[Image #1]` is per turn and repeats: it cannot name a picture for a tool."""
    assert not is_image_id('[Image #1]')
    assert not is_image_id('')


def test_the_id_survives_the_session_file():
    """The whole design rests on this: an id that does not persist names nothing."""
    from pydantic_ai.messages import ModelMessagesTypeAdapter

    messages = [_prompt('mira', _image('img_ab12cd34'))]

    dumped = ModelMessagesTypeAdapter.dump_python(messages, mode='json')
    restored = ModelMessagesTypeAdapter.validate_python(dumped)

    part = restored[0].parts[0]
    assert part.content[1].identifier == 'img_ab12cd34'


# --- finding one -----------------------------------------------------------


def test_an_image_is_found_by_its_id():
    messages = [_prompt('mira', _image('img_aaaaaaaa')), _prompt('otra', _image('img_bbbbbbbb'))]

    found = find_image(messages, 'img_bbbbbbbb')

    assert found is not None
    assert found.identifier == 'img_bbbbbbbb'


def test_an_unknown_id_finds_nothing():
    messages = [_prompt('mira', _image('img_aaaaaaaa'))]

    assert find_image(messages, 'img_deadbeef') is None
    assert find_image(messages, '') is None


def test_the_index_reports_every_id_once():
    messages = [_prompt('mira', _image('img_aaaaaaaa'), _image('img_bbbbbbbb'))]

    index = image_ids(messages)

    assert set(index) == {'img_aaaaaaaa', 'img_bbbbbbbb'}


def test_a_repeated_identifier_resolves_to_the_newest_image():
    """A legacy session saved `[Image #1]` as the identifier, once per turn.

    Ambiguous input must not crash the tool: the newest match is the best guess and
    keeps those sessions usable.
    """
    oldest = _image('[Image #1]')
    newest = _image('[Image #1]')
    messages = [_prompt('primera', oldest), _prompt('segunda', newest)]

    found = find_image(messages, '[Image #1]')

    assert found is not None
    assert found.data == newest.data


def test_messages_without_images_are_ignored():
    """A prompt saved before images existed stores its text as a plain string."""
    messages = [ModelRequest(parts=[UserPromptPart(content='solo texto')])]

    assert image_ids(messages) == {}
