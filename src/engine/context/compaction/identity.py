"""Stable transcript identities shared by planning, projection and replay."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)


def part_key(message: ModelMessage, part: Any, index: int) -> str:
    """Stable content-free handles survive merged request envelopes on resume."""
    if isinstance(part, (ToolCallPart, ToolReturnPart)):
        return f'{part.part_kind}:{part.tool_call_id}'
    prefix = legacy_part_key(message, part, index)
    content = getattr(part, 'content', '')
    if not isinstance(content, str):
        envelope = ModelMessagesTypeAdapter.dump_python([ModelRequest(parts=[part])], mode='json')[0]
        content = json.dumps(envelope['parts'][0].get('content'), sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]
    return f'{prefix}:{digest}'


def legacy_part_key(message: ModelMessage, part: Any, index: int) -> str:
    """Version 2/3 key, read only to migrate stored decisions and their anchors."""
    if isinstance(part, (ToolCallPart, ToolReturnPart)):
        return f'{part.part_kind}:{part.tool_call_id}'
    timestamp = getattr(part, 'timestamp', None) or getattr(message, 'timestamp', None)
    return f'{getattr(part, "part_kind", "part")}:{timestamp}:{index if not isinstance(part, UserPromptPart) else 0}'


def boundary_index(messages: Sequence[ModelMessage], boundary: str | None) -> int:
    """Index of the first message the summary does not replace, or ``0`` if unknown."""
    if not boundary:
        return 0
    for index, message in enumerate(messages):
        for offset, part in enumerate(message.parts):
            if part_key(message, part, offset) == boundary:
                return index
    return 0
