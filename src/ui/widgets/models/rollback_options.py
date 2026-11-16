"""Helpers that used to live in rollback_screen."""

from __future__ import annotations

from rich.text import Text
from textual.widgets.option_list import Option

from src.ui.widgets.models.point import Point

ROLLBACK_FILES = 'rollback_files'


def point_option(point: Point, *, index: int) -> Option:
    """One row: the moment, what was asked, and what a rollback would cost."""
    line = Text()
    line.append(f'{index:>2}. ', style='dim')
    line.append(point.checkpoint.at[11:19], style='bold cyan')
    line.append('  ')
    line.append(point.preview or '(empty prompt)', style='')
    if point.files is not None:
        if point.files:
            line.append(f'  · {point.files} file{"" if point.files == 1 else "s"}', style='yellow')
        else:
            line.append('  · no files', style='green')
    return Option(line, id=point.checkpoint.turn_id)


def action_option(kind: str, label: str, detail: str) -> Option:
    line = Text()
    line.append(label, style='bold red' if kind == ROLLBACK_FILES else 'bold')
    line.append(f'  — {detail}', style='dim')
    return Option(line, id=kind)
