"""Image identity and context token estimates."""

import secrets

PATCH = 28
"""Side of the square a provider divides the image into before counting tokens."""

BASE_IMAGE_TOKENS = 1
"""The floor: one patch is the cheapest an image can be."""

IMAGE_ID_BYTES = 4
"""Entropy of an image id: eight hex characters, enough to never collide in a session."""


def new_image_id() -> str:
    """A short id for one image, unique within a session with overwhelming odds.

    It is what the model is told instead of being handed the pixels again, and what
    `view_image` takes to bring the picture back.
    """
    return f'img_{secrets.token_hex(IMAGE_ID_BYTES)}'


def image_tokens(width: int, height: int) -> int:
    """Estimated tokens for an image of ``width``×``height`` pixels.

    Patches, not bytes: a 40 KB screenshot of a dense table costs the same as a
    4 MB photograph of the same size, because the model looks at the same number
    of pixels either way.
    """
    if width <= 0 or height <= 0:
        return BASE_IMAGE_TOKENS
    across = -(-width // PATCH)
    down = -(-height // PATCH)
    return max(BASE_IMAGE_TOKENS, across * down)
