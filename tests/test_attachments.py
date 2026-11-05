"""The image attachments: what the user pastes, as the model receives it.

Three properties are pinned here, and each one has a failure mode that is
invisible until it reaches a provider:

- **Format and size.** A pasted screenshot is 4K and several megabytes. It is
  re-encoded to a bounded edge, because an image that is too large is rejected by
  the provider in the middle of a turn, after the user typed everything.
- **Cost.** The context meter has to know what an image costs. `str()` of a
  `BinaryContent` is a short repr, so estimating from it says an image costs ten
  tokens and the budget is wrong from the first screenshot.
- **Identity.** The `[Image #n]` token in the text is the only link between what
  the user wrote and the bytes that travel with it. A mismatch means the model
  reads a caption for an image it never received.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from src.engine.attachments.image_attachment import ImageAttachment, image_tokens
from src.engine.attachments.images import (
    MAX_IMAGE_EDGE,
    ImageError,
    prepare_image,
)


def _png(width: int, height: int, *, mode: str = 'RGB') -> bytes:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), color='#3465a4').save(buffer, format='PNG')
    return buffer.getvalue()


def _jpeg(width: int, height: int) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color='#3465a4').save(buffer, format='JPEG')
    return buffer.getvalue()


# --- prepare_image ----------------------------------------------------------


def test_a_small_png_keeps_its_size_and_becomes_a_png_attachment():
    attachment = prepare_image(_png(64, 48))

    assert attachment.media_type == 'image/png'
    assert (attachment.width, attachment.height) == (64, 48)


def test_a_large_image_is_downscaled_to_the_maximum_edge():
    """A 4K screenshot is 3840 px wide: the provider rejects it or bills it as several tiles."""
    attachment = prepare_image(_png(3000, 1500))

    assert max(attachment.width, attachment.height) == MAX_IMAGE_EDGE
    # The aspect ratio survives the resize, or the model reads a stretched image.
    assert round(attachment.width / attachment.height, 2) == 2.0


def test_a_gif_keeps_the_first_frame_instead_of_failing():
    """Screen recorders and some pickers copy animated GIFs; the model still gets a still."""
    buffer = io.BytesIO()
    frames = [Image.new('RGB', (32, 32), color) for color in ('red', 'blue')]
    frames[0].save(buffer, format='GIF', save_all=True, append_images=frames[1:])

    attachment = prepare_image(buffer.getvalue())

    assert (attachment.width, attachment.height) == (32, 32)
    assert attachment.media_type in {'image/png', 'image/jpeg'}


def test_an_image_with_transparency_stays_png():
    """Flattening a transparent logo onto white changes what the model sees."""
    attachment = prepare_image(_png(40, 40, mode='RGBA'))

    assert attachment.media_type == 'image/png'


def test_a_jpeg_without_transparency_is_compressed_rather_than_stored_raw():
    attachment = prepare_image(_jpeg(1200, 800))

    assert attachment.media_type == 'image/jpeg'
    assert len(attachment.data) < len(_jpeg(1200, 800))


def test_data_that_is_not_an_image_is_refused_with_a_readable_reason():
    with pytest.raises(ImageError) as caught:
        prepare_image(b'not an image at all')

    assert 'image' in str(caught.value).lower()


def test_an_empty_clipboard_payload_is_refused():
    with pytest.raises(ImageError):
        prepare_image(b'')


# --- cost -------------------------------------------------------------------


def test_an_image_costs_by_area_not_by_bytes():
    """Tokens scale with the pixels the model has to look at, not with the file size."""
    # 1568 / 28 = 56 patches per side, and the four images of a quarter of the
    # side cost a quarter of the tokens: the area, then, not the file.
    assert image_tokens(1568, 1568) == 56 * 56
    assert image_tokens(784, 784) == 28 * 28
    assert image_tokens(1568, 1568) == 4 * image_tokens(784, 784)


def test_a_tiny_image_costs_at_least_one_cheap_unit():
    assert image_tokens(8, 8) == 1


# --- the attachment ---------------------------------------------------------


def test_the_token_matches_the_position_the_image_travels_in():
    attachment = ImageAttachment(number=2, data=_png(16, 16), media_type='image/png', width=16, height=16)

    assert attachment.token == '[Image #2]'
    assert attachment.estimated_tokens > 0


def test_the_attachment_becomes_binary_content_with_its_media_type():
    attachment = prepare_image(_png(16, 16))

    content = attachment.to_content()

    assert content.media_type == 'image/png'
    assert content.data == attachment.data
    assert content.is_image


def test_the_label_names_the_format_and_the_size_for_the_chat():
    attachment = prepare_image(_png(640, 480))

    assert attachment.describe() == '640×480 PNG'
