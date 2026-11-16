"""The prompt_of_turn helper of the agents layer."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from src.core.storage.database import Database

if TYPE_CHECKING:
    from pydantic_ai.messages import BinaryContent


PROMPT_KIND = 'user-prompt'


def prompt_of_turn(database: Database, turn_id: str) -> tuple[str, list[BinaryContent]] | None:
    """The prompt that opened a turn: the text the user typed and its images.

    It is what a rollback gives back — the preview on the list and the message the
    composer is refilled with — so both read it from here, and a turn is identified
    by its prompt instead of by whatever message happens to come first.

    The lookup is filtered in SQL so a turn of two hundred steps costs one row and
    not two hundred: the payload is JSON text and the part kind is a literal in it.
    The full scan behind it is the rare path — a result whose arguments happen to
    quote a prompt.
    """
    marker = f'%"part_kind":"{PROMPT_KIND}"%'
    row = database.one(
        'SELECT payload FROM messages WHERE turn_id = ? AND payload LIKE ? ORDER BY seq LIMIT 1',
        (turn_id, marker),
    )
    found = None if row is None else _prompt_in(str(row['payload']))
    if found is not None:
        return found
    for candidate in database.query('SELECT payload FROM messages WHERE turn_id = ? ORDER BY seq', (turn_id,)):
        found = _prompt_in(str(candidate['payload']))
        if found is not None:
            return found
    return None


def _prompt_in(raw: str) -> tuple[str, list[BinaryContent]] | None:
    """The prompt a serialized message carries, or ``None`` when it carries none."""
    from pydantic_ai.messages import BinaryContent

    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    for part in payload.get('parts') or []:
        if not isinstance(part, dict) or part.get('part_kind') != PROMPT_KIND:
            continue
        content = part.get('content')
        if isinstance(content, str):
            return content, []
        if isinstance(content, list):
            text = ' '.join(item for item in content if isinstance(item, str))
            images = [
                BinaryContent(
                    data=item['data'],
                    media_type=item.get('media_type', 'application/octet-stream'),
                    identifier=item.get('identifier'),
                )
                for item in content
                if isinstance(item, dict)
                and item.get('kind') == 'binary'
                and str(item.get('media_type', '')).startswith('image/')
            ]
            return text, images
    return None
