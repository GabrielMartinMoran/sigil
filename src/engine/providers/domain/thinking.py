"""The reasoning effort choices Sigil can send.

pydantic-ai exposes a unified ``thinking`` setting whose string levels are these
six. Not every model accepts every level: the catalogue reports which ones a
given model does, and the UI only offers those. ``DEFAULT`` and ``OFF`` are not
levels — they are the two decisions that are not a level at all, and they keep
the value space closed so nothing silently sends a typo as an effort.
"""

from __future__ import annotations

from typing import Literal

type ThinkingEffort = Literal['minimal', 'low', 'medium', 'high', 'xhigh', 'max']
"""One reasoning level."""

type ThinkingChoice = Literal['default', 'off'] | ThinkingEffort
"""What the user picked: a level, the model default, or reasoning off."""

EFFORT_LEVELS: tuple[ThinkingEffort, ...] = ('minimal', 'low', 'medium', 'high', 'xhigh', 'max')
"""The levels in increasing order of reasoning. The order drives every menu."""

DEFAULT_CHOICE: ThinkingChoice = 'default'

_NON_LEVELS: frozenset[str] = frozenset({'default', 'off'})


def is_effort_level(value: str) -> bool:
    return value in EFFORT_LEVELS


def parse_choice(value: str) -> ThinkingChoice | None:
    """Parses a user string (case and spaces tolerant). ``None`` if unknown."""
    token = value.strip().lower()
    if token in _NON_LEVELS or token in EFFORT_LEVELS:
        return token  # type: ignore[return-value]
    return None


def settings_value(choice: ThinkingChoice) -> object | None:
    """Maps the choice to the ``thinking`` value of ``ModelSettings``.

    ``'default'`` means "send nothing": the model keeps whatever it does on its
    own. ``'off'`` maps to ``False``, which pydantic-ai translates into the
    lowest effort a model accepts when it cannot turn reasoning off.
    """
    if choice == 'default':
        return None
    if choice == 'off':
        return False
    return choice


def label(choice: ThinkingChoice) -> str:
    """Short, human wording for the side panel."""
    return 'default' if choice == 'default' else choice
