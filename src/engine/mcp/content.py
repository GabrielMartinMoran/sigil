"""Normalization of the content an MCP server returns.

A resource can come back as ``str``, ``BinaryContent`` or a list of both.
The model needs a single, predictable payload, so it is flattened here.
"""

from __future__ import annotations

import base64
from typing import Any

from pydantic_ai.messages import BinaryContent

from src.engine.mcp.tool_models.resource_content import ResourceContent


def content_text(content: Any) -> str:
    """Text of a prompt message, whatever its shape."""
    text = getattr(content, 'content', None)
    return text if isinstance(text, str) else str(content)


def to_content(server: str, uri: str, result: Any) -> ResourceContent:
    """Normalizes ``str | BinaryContent | list[...]`` into a single payload."""
    if isinstance(result, list):
        return _join_contents(server, uri, result)
    if isinstance(result, BinaryContent):
        return ResourceContent(
            server=server,
            uri=uri,
            mime_type=result.media_type,
            binary=True,
            encoding='base64',
            content=base64.b64encode(result.data).decode('ascii'),
        )
    return ResourceContent(server=server, uri=uri, content=str(result))


def _join_contents(server: str, uri: str, parts: list[Any]) -> ResourceContent:
    """Flattens a list of contents into a single payload."""
    if len(parts) == 1:
        return to_content(server, uri, parts[0])

    chunks: list[str] = []
    has_binary = False
    media_type: str | None = None
    for part in parts:
        if isinstance(part, BinaryContent):
            has_binary = True
            media_type = media_type or part.media_type
            encoded = base64.b64encode(part.data).decode('ascii')
            chunks.append(f'[binary {part.media_type}, base64]\n{encoded}')
        else:
            chunks.append(str(part))

    return ResourceContent(
        server=server,
        uri=uri,
        mime_type=media_type,
        binary=has_binary,
        encoding='base64' if has_binary else 'utf-8',
        content='\n\n'.join(chunks),
    )
