"""Format an image marker."""


def token(number: int) -> str:
    """The marker for the ``number``-th image of a message (1-based)."""
    return f'[Image #{number}]'
