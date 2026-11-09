"""Recognize persisted subagent reports without mistaking arbitrary user text for one."""

from __future__ import annotations

from collections.abc import Sequence

from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.summoner import _envelope

WAKE_PROMPT = 'A subagent finished while the session was idle. Read its report and continue.'


def split_reports(text: str, nodes: Sequence[AgentNode]) -> tuple[list[AgentNode], str]:
    """Remove only complete reports matching a known execution and its saved result."""
    reports: list[AgentNode] = []
    remaining = text
    while remaining:
        for node in nodes:
            if node.is_root or node.result is None:
                continue
            report = _envelope(node, node.result)
            if remaining == report:
                reports.append(node)
                remaining = ''
                break
            prefix = report + '\n\n'
            if remaining.startswith(prefix):
                reports.append(node)
                remaining = remaining[len(prefix) :]
                break
        else:
            break
    if reports and remaining == WAKE_PROMPT:
        remaining = ''
    return reports, remaining
