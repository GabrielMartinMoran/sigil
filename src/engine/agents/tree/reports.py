"""Recognize persisted subagent reports without mistaking arbitrary user text for one.

A subagent's answer reaches the parent as an ordinary `user-prompt` part: the
envelope `_envelope` builds, followed by the wake sentence. That is deliberate — it
is a durable row, so a service that dies before reading it leaves an interrupted
turn instead of losing the answer. The consequence is that the *projection* has to
recognize the envelope, because a client holding projected items cannot tell a
report from something the user typed.

The recognition is an exact match against a saved result of a known node, so text a
person typed is never hidden: it would have to be byte-identical to an envelope the
tree already recorded, and even then the remaining text is still painted.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.summoner import _envelope

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
        'text': node.result or '',
    }


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
    if reports and remaining.startswith(WAKE_PROMPT):
        # The sentence is the service telling itself to read, not a turn: it is dropped
        # with the envelope it came with. Anything written after it stays, so a report
        # that arrived beside a message still shows both.
        remaining = remaining[len(WAKE_PROMPT) :].lstrip('\n')
    return reports, remaining


__all__ = ['WAKE_PROMPT', 'report_view', 'split_reports']
