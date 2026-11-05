"""Turning clipboard bytes into an image a provider will accept.

Two jobs, and both exist because the provider is unforgiving:

- **Normalize.** Screenshots arrive as 4K PNGs of several megabytes, and some
  desktops hand over BMP or HEIC. Providers cap the edge length, the file size and
  the accepted media types, and they do it with a 400 in the middle of a turn,
  after the user has already typed the question. The image is bounded here.
- **Price it.** The context meter has to know what an image costs before it is
  sent. Tokens scale with the pixels the model looks at, so the estimate is
  derived from the final size and not from the file size.

The encoding rule favors the screenshot, which is what people actually paste: a
PNG stays a PNG, because recompressing a terminal or a diff as JPEG puts ringing
artifacts around every glyph. JPEG is used for payloads that are already JPEG (a
re-encode to PNG would balloon them) and as the fallback when a lossless payload
exceeds the byte cap.

What an image costs in the meter is on `ImageAttachment.estimated_tokens`: the
result here is already an attachment, priced from its final size.
"""

from __future__ import annotations

import io

from PIL import Image, UnidentifiedImageError

from src.engine.attachments.image_attachment import ImageAttachment

MAX_IMAGE_EDGE = 1568
"""Longest side kept, in pixels.

The same number Anthropic documents and OpenAI's image guidance uses: above it the
provider rescales anyway, so sending more only costs bandwidth and tokens.
"""

MAX_IMAGE_BYTES = 4 * 1024 * 1024
"""Ceiling for the re-encoded payload.

Providers reject anything above ~5 MB per image; the margin is for the base64
expansion that happens on the wire.
"""

JPEG_QUALITY = 85
"""Quality for photographs. Visually lossless for text at this size, half the bytes."""

JPEG_QUALITY_STEPS = (70, 55, 40)
"""Quality ladder used when even the bounded payload exceeds the byte cap."""

PNG_MEDIA_TYPE = 'image/png'
JPEG_MEDIA_TYPE = 'image/jpeg'

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'


class ImageError(ValueError):
    """The bytes cannot be turned into an image a model can read."""


def prepare_image(raw: bytes) -> ImageAttachment:
    """Validates, re-encodes and bounds ``raw``.

    Args:
        raw: Bytes as they came out of the clipboard or a file.

    Raises:
        ImageError: When the payload is empty, is not an image, or the image
            cannot be decoded.
    """
    if not raw:
        raise ImageError('The clipboard had no image in it.')

    source_format = ''
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            # A GIF or a multi-page TIFF carries several images; the model reads
            # one, and the first frame is the one the preview showed.
            opened.seek(0)
            source_format = (opened.format or '').upper()
            image = opened.copy()
    except UnidentifiedImageError as exc:
        raise ImageError('That paste is not an image the model can read.') from exc
    except OSError as exc:
        # Truncated or corrupt: Pillow raises here for a payload that looked
        # like a PNG and is not one the whole way through.
        raise ImageError(f'That image could not be decoded: {exc}') from exc

    bounded = _bounded(image)
    data, media_type = _encode(bounded, source_format)
    return ImageAttachment(number=0, data=data, media_type=media_type, width=bounded.width, height=bounded.height)


def _bounded(image: Image.Image) -> Image.Image:
    """Scales the image down so its longest side fits `MAX_IMAGE_EDGE`."""
    longest = max(image.size)
    if longest <= MAX_IMAGE_EDGE:
        return image
    ratio = MAX_IMAGE_EDGE / longest
    size = (max(1, round(image.width * ratio)), max(1, round(image.height * ratio)))
    # `LANCZOS` keeps text legible when a screenshot is shrunk; the default
    # nearest-neighbor turns a terminal screenshot into noise.
    return image.resize(size, Image.Resampling.LANCZOS)


def _encode(image: Image.Image, source_format: str) -> tuple[bytes, str]:
    """Encodes the bounded image, preferring the lossless route unless it is a photo.

    Returns:
        The payload and its media type.

    Raises:
        ImageError: When no encoding fits in `MAX_IMAGE_BYTES`.
    """
    if source_format == 'JPEG':
        # Already lossy: decode-and-recompress as PNG would multiply its size.
        return _jpeg_within_cap(image.convert('RGB'))

    data = _to_png(image)
    if len(data) <= MAX_IMAGE_BYTES:
        return data, PNG_MEDIA_TYPE
    return _jpeg_within_cap(_flatten(image))


def _flatten(image: Image.Image) -> Image.Image:
    """Drops transparency onto black, the background a screenshot assumes."""
    if image.mode in {'RGBA', 'LA', 'PA'} or (image.mode == 'P' and 'transparency' in image.info):
        rgba = image.convert('RGBA')
        background = Image.new('RGB', rgba.size, (0, 0, 0))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert('RGB')


def _jpeg_within_cap(image: Image.Image) -> tuple[bytes, str]:
    """JPEG at `JPEG_QUALITY`, or at the first ladder step that fits the cap."""
    payload = _to_jpeg(image)
    if len(payload) <= MAX_IMAGE_BYTES:
        return payload, JPEG_MEDIA_TYPE
    for quality in JPEG_QUALITY_STEPS:
        payload = _to_jpeg(image, quality=quality)
        if len(payload) <= MAX_IMAGE_BYTES:
            return payload, JPEG_MEDIA_TYPE
    raise ImageError('That image is too large to send even after compressing it.')


def _to_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    return buffer.getvalue()


def _to_jpeg(image: Image.Image, *, quality: int = JPEG_QUALITY) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format='JPEG', quality=quality, optimize=True)
    return buffer.getvalue()
