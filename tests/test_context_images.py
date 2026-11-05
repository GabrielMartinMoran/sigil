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
from pydantic_ai.messages import BinaryImage, ModelRequest, TextPart, UserPromptPart

from src.engine.attachments.image_attachment import image_tokens
from src.engine.context.images import image_part_tokens
from src.engine.context.strategy.rewriting import IMAGE_OMITTED, drop_old_images
from src.engine.context.strategy.windowed import WindowedStrategy
from src.engine.context.tokens import estimate_message_tokens, part_tokens


def _png(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color='#3465a4').save(buffer, format='PNG')
    return buffer.getvalue()


def _image(width: int = 1000, height: int = 1000) -> BinaryImage:
    return BinaryImage(data=_png(width, height), media_type='image/png')


def _prompt(*content: object) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=list(content))])


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
    turns = [_turn('vieja', _image()), _turn('reciente', _image())]

    messages, dropped = drop_old_images(turns, recent_start=1)

    assert dropped == 1
    kept = messages[0].parts[0].content
    assert not any(isinstance(item, BinaryImage) for item in kept)
    assert IMAGE_OMITTED.format(number=1, s='') in kept


def test_an_image_from_the_recent_turn_stays():
    """What the conversation is about right now cannot be dropped."""
    turns = [_turn('reciente', _image())]

    messages, dropped = drop_old_images(turns, recent_start=0)

    assert dropped == 0
    assert any(isinstance(item, BinaryImage) for item in messages[0].parts[0].content)


def test_dropping_the_images_keeps_the_text_of_the_message():
    turns = [_turn('mirá [Image #1] esto', _image()), _turn('otra')]

    messages, _ = drop_old_images(turns, recent_start=1)

    assert messages[0].parts[0].content[0] == 'mirá [Image #1] esto'


def test_a_text_only_history_is_untouched():
    turns = [[_prompt('sin imágenes')]]

    messages, dropped = drop_old_images(turns, recent_start=0)

    assert dropped == 0
    assert messages[0] is turns[0][0]


# --- the strategy as a whole ------------------------------------------------


def test_the_window_drops_images_only_from_the_old_turns():
    """One turn per prompt: `split_turns` cuts on every `UserPromptPart`."""
    from src.engine.context.budget import ContextBudget

    turns = []
    for index in range(4):
        turns.append(_prompt(f'turno {index}', _image()))
        turns.append(ModelRequest(parts=[TextPart(content='sigo')]))

    strategy = WindowedStrategy(keep_recent_turns=1, drop_thinking=False)
    result = strategy.view(turns, ContextBudget(window=200_000, output_reserve=0))

    # Four turns, the last one kept whole.
    assert result.dropped_images == 3
    kept = [
        item
        for message in result.messages
        for part in message.parts
        for item in (part.content if isinstance(getattr(part, 'content', None), list) else [])
        if isinstance(item, BinaryImage)
    ]
    assert len(kept) == 1


def test_a_message_without_images_reports_nothing_dropped():
    strategy = WindowedStrategy(keep_recent_turns=1, drop_thinking=False)
    from src.engine.context.budget import ContextBudget

    result = strategy.view(
        [_prompt('hola'), ModelRequest(parts=[TextPart(content='chau')])],
        ContextBudget(window=200_000, output_reserve=0),
    )

    assert result.dropped_images == 0
