"""Blank rows between two parts of the tree."""

from __future__ import annotations

from enum import Enum


class Gap(Enum):
    """A row of the list that is not a node: a label, or the empty state."""

    HISTORY = 'history'
    EMPTY = 'empty'
