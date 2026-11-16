"""The input_views helper of the agents layer."""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelMessage, UserPromptPart

from src.core.agents.type_of import type_of


def input_views(message: ModelMessage, part: UserPromptPart) -> list[dict[str, Any]]:
    """Shared visibility policy for terminal and web transcript adapters."""
    classification = type_of(message, part)
    kind = classification['type']
    if kind == 'internal_notice':
        return []
    if kind != 'subagent_report':
        return [{'kind': kind, 'content': part.content}]
    items = [{**snapshot, 'kind': kind} for snapshot in classification.get('reports', [])]
    if classification.get('user_text'):
        content = classification['user_text']
        if not isinstance(part.content, str):
            content = [content, *(item for item in part.content if not isinstance(item, str))]
        items.append({'kind': 'user', 'content': content})
    return items
