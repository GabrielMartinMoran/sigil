"""Durable snapshots of subagent reports, independent of later node changes."""

from __future__ import annotations

from typing import Any

from src.core.agents.tree.models.agent_node import AgentNode

WAKE_PROMPT = 'A subagent finished while the session was idle. Read its report and continue.'


def report_view(node: AgentNode) -> dict[str, Any]:
    """One report as a projection item a client can paint without the node.

    The three fields are what the terminal's report line and the web card draw, and
    the id is what makes the line clickable: it opens that execution.
    """
    return {
        'kind': 'subagent_report',
        'node_id': node.id,
        'label': node.label,
        'short_id': node.short_id,
        'name': node.name,
        'status': str(getattr(node.status, 'value', node.status)),
        'text': node.result or node.error or '',
    }


__all__ = ['WAKE_PROMPT', 'report_view']
