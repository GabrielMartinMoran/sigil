"""Whether a catalogue declared an image input."""

from __future__ import annotations


def accepts_image_input(modalities: object) -> bool:
    """Whether a catalogue's declared input modalities include an image.

    Two spellings for the same thing, and neither is stable: OpenCode publishes
    MIME types (image/png, image/jpeg, image/webp), OpenRouter publishes the bare
    family (image), and the next provider will publish something else. Splitting on
    the slash and comparing the family is what keeps a MIME-qualified list from
    making every multimodal OpenCode model blind.

    A list that mentions no image is text-only and refuses the paste. Anything
    that is not a list is silence, and silence answers True: refusing a paste a
    model would have accepted is the worse mistake.
    """
    if not isinstance(modalities, list):
        return True
    return any(isinstance(token, str) and token.strip().lower().split('/')[0] == 'image' for token in modalities)
