"""Project request payloads without reducing the original transcript.

Only content-free preparation metadata is persisted on request/response envelopes.
The strategy and debug projection are pure; the request hook owns recording and
budget enforcement. No original part, instructions, or tool payload is rewritten.
"""

from __future__ import annotations


def subagent_window_warning(node_id: str, model: str) -> str:
    return (
        f'Subagent #{node_id[:8]} ({model}): context window is unknown; '
        'its requests cannot be checked against the model limit.'
    )
