"""Tests for the image cache: turns, the window and the pixel cap.

The rule is one line: an image is loaded in the turn it arrived in and stays
loaded for a few turns after that. Everything else here exists to make that
sentence safe.
"""

from __future__ import annotations

import io

from PIL import Image
from pydantic_ai.messages import (
    BinaryImage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.context.images_cache import ImageVerdict, decide, occurrences

# --- helpers ---------------------------------------------------------------


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color='#3465a4').save(buffer, format='PNG')
    return buffer.getvalue()


def _image(identifier: str, size: int = 64) -> BinaryImage:
    return BinaryImage(data=_png(size, size), media_type='image/png', identifier=identifier)


def _round(run: str, *content: object) -> list:
    """A turn: a prompt and the response that closes it."""
    prompt = list(content) if content else f'turn {run}'
    return [
        ModelRequest(parts=[UserPromptPart(content=prompt)], run_id=run),
        ModelResponse(parts=[], run_id=run),
    ]


def _viewed(run: str, identifier: str, size: int = 64) -> list:
    """A turn where a tool handed one image back."""
    return [
        ModelRequest(parts=[UserPromptPart(content=f'look at {identifier}')], run_id=run),
        ModelResponse(
            parts=[ToolCallPart(tool_name='view_image', args={'source': identifier}, tool_call_id='c')],
            run_id=run,
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name='view_image', content=[_image(identifier, size)], tool_call_id='c')],
            run_id=run,
        ),
    ]


def _flat(*turns: list) -> list:
    """The history as pydantic-ai takes it: a flat list of messages."""
    return [message for turn in turns for message in turn]


def _position_of_viewed(messages: list) -> tuple[int, int]:
    """Where the image inside the `view_image` result sits."""
    for message_index, message in enumerate(messages):
        for part in message.parts:
            if isinstance(part, ToolReturnPart) and isinstance(part.content, list):
                for item_index, item in enumerate(part.content):
                    if isinstance(item, BinaryImage):
                        return (message_index, item_index)
    raise AssertionError('no image in a tool result')


# --- occurrences -----------------------------------------------------------


def test_an_image_is_stamped_with_the_round_that_carried_it():
    messages = _flat(
        _round('r1', 'aca esta', _image('img_aaaaaaaa')),
        _round('r2'),
    )

    found = occurrences(messages)

    assert len(found) == 1
    assert found[0].identifier == 'img_aaaaaaaa'
    assert found[0].turn == 'r1'


def test_a_message_without_run_id_ages_by_turn():
    """A session saved before `run_id` existed must not keep images forever."""
    messages = [
        ModelRequest(parts=[UserPromptPart(content=['a', _image('img_aaaaaaaa')])]),
        ModelRequest(parts=[UserPromptPart(content='b')]),
        ModelRequest(parts=[UserPromptPart(content='c')]),
    ]

    found = occurrences(messages)

    assert found[0].turn is None
    assert found[0].turn_index == 0


# --- the turn window ------------------------------------------------------


def test_the_round_in_progress_always_travels():
    messages = _flat(_round('r1', 'aca esta', _image('img_aaaaaaaa')))

    verdicts = decide(messages, turn_window=1, max_tokens=None)

    assert set(verdicts.values()) == {ImageVerdict.KEEP}


def test_an_image_older_than_the_window_is_expired():
    messages = _flat(
        _round('r1', 'aca esta', _image('img_aaaaaaaa')),
        _round('r2'),
        _round('r3'),
    )

    verdicts = decide(messages, turn_window=2, max_tokens=None)

    assert set(verdicts.values()) == {ImageVerdict.EXPIRED}


def test_the_window_counts_from_the_newest_copy():
    """Fetching a picture again is what moves it forward: it is relevant again."""
    messages = _flat(
        _round('r1', 'aca esta', _image('img_aaaaaaaa')),
        _round('r2'),
        _round('r3'),
        _viewed('r4', 'img_aaaaaaaa'),
    )

    verdicts = decide(messages, turn_window=2, max_tokens=None)

    assert verdicts[_position_of_viewed(messages)] is ImageVerdict.KEEP


def test_a_short_window_still_keeps_the_round_in_progress():
    """`turn_window=0` is not a way to keep nothing: the current turn always travels."""
    messages = _flat(_round('r1', 'aca esta', _image('img_aaaaaaaa')))

    verdicts = decide(messages, turn_window=0, max_tokens=None)

    assert set(verdicts.values()) == {ImageVerdict.KEEP}


# --- the same picture twice ------------------------------------------------


def test_two_copies_of_one_picture_charge_once():
    messages = _flat(
        _round('r1', 'aca esta', _image('img_aaaaaaaa')),
        _viewed('r2', 'img_aaaaaaaa'),
    )

    verdicts = decide(messages, turn_window=5, max_tokens=None)

    assert list(verdicts.values()).count(ImageVerdict.KEEP) == 1
    assert list(verdicts.values()).count(ImageVerdict.SUPERSEDED) == 1


def test_the_superseded_one_is_the_oldest():
    messages = _flat(
        _round('r1', 'aca esta', _image('img_aaaaaaaa')),
        _viewed('r2', 'img_aaaaaaaa'),
    )

    verdicts = decide(messages, turn_window=5, max_tokens=None)

    assert verdicts[min(verdicts)] is ImageVerdict.SUPERSEDED


# --- the pixel cap ---------------------------------------------------------


def test_the_cap_drops_the_oldest_until_the_pixels_fit():
    # Each is ~1,296 tokens at 1000x1000.
    messages = _flat(
        _round('r1', 'uno', _image('img_aaaaaaaa', 1000)),
        _round('r2', 'dos', _image('img_bbbbbbbb', 1000)),
        _round('r3', 'tres', _image('img_cccccccc', 1000)),
    )

    verdicts = decide(messages, turn_window=10, max_tokens=3_000)

    assert list(verdicts.values()).count(ImageVerdict.KEEP) == 2
    assert list(verdicts.values()).count(ImageVerdict.OVER_BUDGET) == 1


def test_without_a_cap_the_window_is_the_only_limit():
    turns = [_round(f'r{n}', f'turno {n}', _image(f'img_{n:08x}', 1000)) for n in range(3)]

    verdicts = decide(_flat(*turns), turn_window=10, max_tokens=None)

    assert set(verdicts.values()) == {ImageVerdict.KEEP}


def test_no_images_means_no_work():
    assert decide(_flat(_round('r1')), turn_window=3, max_tokens=None) == {}
