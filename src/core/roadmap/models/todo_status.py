"""Possible states of a roadmap task."""

from __future__ import annotations

from typing import Literal

TodoStatus = Literal['pending', 'in_progress', 'done', 'blocked']

TODO_STATUSES: tuple[TodoStatus, ...] = ('pending', 'in_progress', 'done', 'blocked')
