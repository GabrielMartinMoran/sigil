"""Helpers that used to live in session_picker."""

from __future__ import annotations

from datetime import datetime

from rich.text import Text
from textual.widgets.option_list import Option

from src.core.sessions.catalog import CatalogEntry


def session_option(entry: CatalogEntry) -> Option:
    """One line per session: local update time, workspace, short id and title.

    The badge is what tells two sessions with the same title apart, which is
    exactly what happens when the same task is tried in the main worktree and in a
    worktree of its own. The title is already cut to one line
    (``Session.display_title``), so the row keeps a fixed shape whether the session
    was named or not.
    """
    badge = Text(f'{entry.label:<12}', style='bold yellow' if entry.is_worktree else 'bold cyan')
    marker = Text('● ' if entry.is_current else '  ', style='green')
    try:
        updated = datetime.fromisoformat(entry.session.updated_at).astimezone().strftime('%Y-%m-%d %H:%M')
    except ValueError:
        updated = 'unknown date    '
    prompt = Text(f'{updated}  ', style='dim')
    prompt.append(marker)
    prompt.append(badge)
    prompt.append(f'{entry.session.short_id}  ', style='bold')
    prompt.append(entry.session.display_title or '(empty)', style='dim')
    return Option(prompt, id=entry.session.id)
