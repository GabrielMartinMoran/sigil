"""The ConfigScope value of the agents layer."""

from __future__ import annotations

from enum import StrEnum


class ConfigScope(StrEnum):
    """Which of the two files an entry belongs to.

    ``PROJECT`` wins over ``GLOBAL``: the order here is the precedence order, and
    the state below relies on it instead of restating it.
    """

    PROJECT = 'project'
    GLOBAL = 'global'
