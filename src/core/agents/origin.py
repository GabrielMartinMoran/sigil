"""The origin helper of the agents layer."""

from __future__ import annotations

from typing import Any, Literal

MessageType = Literal['user', 'agent_message', 'subagent_report', 'internal_notice']


def origin(message_type: MessageType = 'user', *, reports: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {'type': message_type}
    if reports is not None:
        result['reports'] = reports
    return result
