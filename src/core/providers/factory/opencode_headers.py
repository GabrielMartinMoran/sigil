"""The opencode_headers helper of the providers layer."""

from __future__ import annotations

from src.core.providers.factory.builder import (
    SIGIL_VERSION,
)


def opencode_headers(session_id: str) -> dict[str, str]:
    """Headers OpenCode expects from a third-party client."""
    return {'User-Agent': f'sigil/{SIGIL_VERSION}', 'x-opencode-session': session_id}
