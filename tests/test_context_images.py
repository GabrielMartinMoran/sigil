"""What an image costs in the context meter, and what happens when it gets old.

Both are silent failures if they are wrong:

- A model of an image that costs ten tokens makes the meter believe a conversation
  with six screenshots fits in a window it does not fit in. The request then fails
  at the provider, mid-turn.
- An image that is never dropped makes every turn of a long session pay for
  screenshots nobody is talking about any more.
"""

from __future__ import annotations

import io

from PIL import Image
from pydantic_ai.messages import BinaryImage, ModelRequest, TextPart, ToolReturnPart, UserPromptPart

from src.engine.attachments.image_attachment import image_tokens
from src.engine.context.images import image_part_tokens
from src.engine.context.strategy.rewriting import IMAGE_OMITTED, drop_stale_images
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.context.tokens import estimate_message_tokens, part_tokens


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color='#3465a4').save(buffer, format='PNG')
    return buffer.getvalue()


_minted = 0


def _image(width: int = 1000, height: int = 1000) -> BinaryImage:
    """A distinct picture.

    The identifier is minted here on purpose: pydantic-ai derives it from the bytes
    when it is missing, so two fixtures with the same payload would look like the
    same picture — and the dedup would rightly treat them as one.
    """
    global _minted
    _minted += 1
    return BinaryImage(data=_png(width, height), media_type='image/png', identifier=f'img_{_minted:08x}')


def _prompt(*content: object) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=list(content))])


def _flat(turns: list) -> list:
    return [message for turn in turns for message in turn]


def _images_in(messages: list) -> list[BinaryImage]:
    return [
        item
        for message in messages
        for part in message.parts
        for item in (part.content if isinstance(getattr(part, 'content', None), list) else [])
        if isinstance(item, BinaryImage)
    ]


# --- the estimate -----------------------------------------------------------


def test_a_prompt_with_an_image_costs_the_image_and_not_its_repr():
    """`str(BinaryContent)` is a forty-character repr; the pixels are four figures."""
    prompt = _prompt('mira [Image #1]', _image(1000, 1000))

    assert image_part_tokens(_image(1000, 1000)) == image_tokens(1000, 1000)
    assert estimate_message_tokens(prompt) > 1000


def test_the_image_is_not_counted_twice_as_text():
    """The caption is priced as text; the picture only as pixels."""
    with_image = _prompt('una frase', _image(200, 200))
    without = _prompt('una frase')

    difference = estimate_message_tokens(with_image) - estimate_message_tokens(without)

    assert difference == image_tokens(200, 200)


def test_part_tokens_of_a_binary_image_does_not_go_through_the_text_estimate():
    image = _image(300, 300)

    assert part_tokens(image) == image_tokens(300, 300)


# --- dropping the old ones --------------------------------------------------


def _turn(text: str, *images: BinaryImage) -> list[ModelRequest]:
    return [_prompt(text, *images)]


def test_an_image_from_an_old_turn_leaves_the_request_and_is_noted():
    old = _image()
    turns = [_turn('vieja', old), _turn('reciente', _image())]

    messages, dropped = drop_stale_images(_flat(turns), turn_window=1)

    assert dropped == 1
    kept = messages[0].parts[0].content
    assert not any(isinstance(item, BinaryImage) for item in kept)
    assert IMAGE_OMITTED.format(image_id=old.identifier) in kept


def test_an_image_from_the_recent_turn_stays():
    """What the conversation is about right now cannot be dropped."""
    turns = [_turn('reciente', _image())]

    messages, dropped = drop_stale_images(_flat(turns), turn_window=5)

    assert dropped == 0
    assert any(isinstance(item, BinaryImage) for item in messages[0].parts[0].content)


def test_dropping_the_images_keeps_the_text_of_the_message():
    turns = [_turn('mirá [Image #1] esto', _image()), _turn('otra')]

    messages, _ = drop_stale_images(_flat(turns), turn_window=1)

    assert messages[0].parts[0].content[0] == 'mirá [Image #1] esto'


def test_a_text_only_history_is_untouched():
    turns = [[_prompt('sin imágenes')]]

    messages, dropped = drop_stale_images(_flat(turns), turn_window=5)

    assert dropped == 0
    assert messages[0] is turns[0][0]


# --- the strategy as a whole ------------------------------------------------


def test_the_window_drops_images_only_from_the_old_turns():
    """One turn per prompt: `split_prompts` cuts on every `UserPromptPart`."""
    from src.engine.context.budget import ContextBudget

    turns = []
    for index in range(4):
        turns.append(_prompt(f'turno {index}', _image()))
        turns.append(ModelRequest(parts=[TextPart(content='sigo')]))

    strategy = WindowedStrategy(keep_recent_prompts=1, image_turns=1, reasoning_steps=1000)
    result = strategy.view(turns, ContextBudget(window=200_000, output_reserve=0))

    # Four turns, the last one kept whole.
    assert result.dropped_images == 3
    kept = [
        item
        for message in result.messages
        for part in message.parts
        for item in (part.content if isinstance(part.content, list) else [])
        if isinstance(item, BinaryImage)
    ]
    assert len(kept) == 1


def test_images_have_their_own_window_of_turns():
    """A picture outlives the turn it came in: three turns by default, not one.

    The text of a turn can be truncated long before the image stops being useful, so
    the two windows are separate: `keep_recent_prompts` decides what text survives and
    `image_turns` what pixels travel.
    """
    from src.engine.context.budget import ContextBudget

    turns = []
    for index in range(5):
        turns.append(_prompt(f'turno {index}', _image()))
        turns.append(ModelRequest(parts=[TextPart(content='sigo')]))

    result = WindowedStrategy(keep_recent_prompts=1, reasoning_steps=1000).view(
        turns, ContextBudget(window=200_000, output_reserve=0)
    )

    assert result.dropped_images == 2
    kept = [
        item
        for message in result.messages
        for part in message.parts
        for item in (part.content if isinstance(part.content, list) else [])
        if isinstance(item, BinaryImage)
    ]
    assert len(kept) == 3


def test_the_note_names_the_id_that_brings_the_image_back():
    """Dropping the pixels must cost a tool call, not the information."""
    from src.engine.context.budget import ContextBudget

    image = _image()
    turns = [_prompt('vieja', image), _prompt('reciente')]

    result = WindowedStrategy(keep_recent_prompts=1, image_turns=1, reasoning_steps=1000).view(
        turns, ContextBudget(window=200_000, output_reserve=0)
    )

    text = ' '.join(
        item
        for message in result.messages
        for part in message.parts
        for item in (part.content if isinstance(part.content, list) else [])
        if isinstance(item, str)
    )
    assert image.identifier in text
    assert 'view_image' in text


def test_the_pixel_cap_bounds_what_stays():
    """Three turns of 4K screenshots cost more than the conversation they belong to."""
    from src.engine.context.budget import ContextBudget

    turns = [_prompt(f'turno {index}', _image(2000, 2000)) for index in range(3)]

    # 10% of 100k is 10k, and one of these costs 5,184: only one fits.
    result = WindowedStrategy(keep_recent_prompts=3, image_turns=3, image_max_percent=10, reasoning_steps=1000).view(
        turns, ContextBudget(window=100_000, output_reserve=0)
    )

    kept = [
        item
        for message in result.messages
        for part in message.parts
        for item in (part.content if isinstance(part.content, list) else [])
        if isinstance(item, BinaryImage)
    ]
    assert result.dropped_images == 2
    assert len(kept) == 1


def test_a_message_without_images_reports_nothing_dropped():
    from src.engine.context.budget import ContextBudget

    strategy = WindowedStrategy(keep_recent_prompts=1, reasoning_steps=1000)

    result = strategy.view(
        [_prompt('hola'), ModelRequest(parts=[TextPart(content='chau')])],
        ContextBudget(window=200_000, output_reserve=0),
    )

    assert result.dropped_images == 0


# --- compaction reports what it drops ---------------------------------------


def test_compaction_reports_the_images_it_drops_with_the_turn():
    """A compacted turn loses its pixels, and the report says so.

    The summary keeps the intent and the tool trail, not the screenshots. Without
    counting them here, `/context` would show fewer drops than actually happened and
    an image-heavy session would look cheaper than it is.
    """
    from src.engine.context.budget import ContextBudget
    from src.engine.context.strategy.compacting import CompactingStrategy

    messages = [
        _prompt('look at [Image #1]', _image()),
        _prompt('thanks'),
        _prompt('look at [Image #1]', _image()),
        _prompt('thanks'),
        _prompt('last one [Image #1]', _image()),
    ]

    result = CompactingStrategy(keep_recent_prompts=1, reasoning_steps=1000).view(messages, ContextBudget(window=None))

    assert result.dropped_images == 2


# --- an image a tool brought back -------------------------------------------


def _tool_result(image: BinaryImage) -> ModelRequest:
    from pydantic_ai.messages import ToolReturnPart

    return ModelRequest(parts=[ToolReturnPart(tool_name='view_image', content=[image], tool_call_id='c1')])


def test_an_image_inside_a_tool_result_is_priced():
    """`view_image` hands a picture back as part of its answer, and it costs pixels."""
    from pydantic_ai.messages import ToolReturnPart

    from src.engine.context.tokens import part_tokens

    part = ToolReturnPart(tool_name='view_image', content=[_image(1000, 1000)], tool_call_id='c1')

    assert part_tokens(part) >= image_tokens(1000, 1000)


def test_an_image_inside_a_tool_result_is_dropped_like_any_other():
    """Otherwise the picture a tool returned is paid on every request, forever."""
    from src.engine.context.budget import ContextBudget

    image = _image(400, 400)
    turns = [_tool_result(image), _prompt('reciente')]

    result = WindowedStrategy(keep_recent_prompts=1, image_turns=1, reasoning_steps=1000).view(
        turns, ContextBudget(window=200_000, output_reserve=0)
    )

    assert result.dropped_images == 1
    left = [
        item
        for message in result.messages
        for part in message.parts
        for item in (part.content if isinstance(getattr(part, 'content', None), list) else [])
        if isinstance(item, BinaryImage)
    ]
    assert left == []


# --- coming back with view_image --------------------------------------------


def _viewed_image(identifier: str) -> list:
    """A turn where the model asked for the image again."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    return [
        _prompt('mira de nuevo'),
        ModelResponse(parts=[ToolCallPart(tool_name='view_image', args={'image_id': identifier}, tool_call_id='c')]),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name='view_image',
                    content=[_image_of(identifier)],
                    tool_call_id='c',
                )
            ]
        ),
    ]


def _image_of(identifier: str) -> BinaryImage:
    return BinaryImage(data=_png(100, 100), media_type='image/png', identifier=identifier)


def test_the_same_picture_is_not_paid_twice():
    """`view_image` re-attaches what the prompt already carried: send it once."""
    from src.engine.context.budget import ContextBudget

    image = _image_of('img_aaaaaaaa')
    turns = [
        [_prompt('aca esta', image)],
        _viewed_image('img_aaaaaaaa'),
    ]

    flat = [message for turn in turns for message in turn]
    result = WindowedStrategy(keep_recent_prompts=2, image_turns=3, reasoning_steps=1000).view(
        flat, ContextBudget(window=None)
    )

    carried = _images_in(result.messages)
    assert len(carried) == 1
    assert result.dropped_images == 1


def test_the_copy_that_survives_is_the_newest_one():
    """The one the model just looked at is the relevant one, not the old paste."""
    from src.engine.context.budget import ContextBudget

    image = _image_of('img_aaaaaaaa')
    turns = [
        [_prompt('aca esta', image)],
        _viewed_image('img_aaaaaaaa'),
    ]

    flat = [message for turn in turns for message in turn]
    result = WindowedStrategy(keep_recent_prompts=2, image_turns=3, reasoning_steps=1000).view(
        flat, ContextBudget(window=None)
    )

    # The surviving copy sits in the last turn, next to the call that asked for it.
    positions = [
        index
        for index, message in enumerate(result.messages)
        for part in message.parts
        for item in (part.content if isinstance(getattr(part, 'content', None), list) else [])
        if isinstance(item, BinaryImage)
    ]
    assert positions == [len(result.messages) - 1]


def test_viewing_an_image_resets_its_clock():
    """A picture the model asked for is relevant again: it gets a fresh window."""
    from src.engine.context.budget import ContextBudget

    image = _image_of('img_aaaaaaaa')
    # Pasted long ago, viewed now.
    turns = [[_prompt('aca esta', image)], *[[_prompt(f'charla {n}')] for n in range(5)], _viewed_image('img_aaaaaaaa')]

    flat = [message for turn in turns for message in turn]
    result = WindowedStrategy(keep_recent_prompts=6, image_turns=3, reasoning_steps=1000).view(
        flat, ContextBudget(window=None)
    )

    carried = _images_in(result.messages)
    assert len(carried) == 1
    # Without the call it would be nine turns old and gone.
    assert carried[0].identifier == 'img_aaaaaaaa'


def test_a_superseded_copy_says_where_the_live_one_is():
    """The note must not tell the model to fetch what is already attached."""
    from src.engine.context.budget import ContextBudget

    image = _image_of('img_aaaaaaaa')
    turns = [[_prompt('aca esta', image)], _viewed_image('img_aaaaaaaa')]

    flat = [message for turn in turns for message in turn]
    result = WindowedStrategy(keep_recent_prompts=2, image_turns=3, reasoning_steps=1000).view(
        flat, ContextBudget(window=None)
    )

    text = ' '.join(
        item
        for message in result.messages
        for part in message.parts
        for item in (part.content if isinstance(getattr(part, 'content', None), list) else [])
        if isinstance(item, str)
    )
    assert 'attached' in text
