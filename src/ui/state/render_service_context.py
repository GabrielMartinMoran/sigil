"""Helpers that used to live in commands_mixin."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _tokens(value: Any) -> str:
    """A token count the service sent, or a plain statement that it did not."""
    return f'{value:,}' if isinstance(value, int) else 'not reported by the service'


def _percent(value: Any) -> str:
    return f'{value:.0f}%' if isinstance(value, (int, float)) else 'not reported'


def render_service_context(context: Mapping[str, Any], workspace: Mapping[str, Any] | None = None) -> str:
    """The `/context` report, built from the numbers the service measured.

    Nothing here is measured locally: the session belongs to the service, and one
    conversation cannot have two meters that disagree. A datum the service did not
    send is named as absent rather than printed as a zero, because a zero is a
    claim about the session and this terminal is in no position to make it. That is
    also why a cold session reads `0 (0%)` and never `?`: the service sends the real
    zero for a session that has not made a request yet.

    The scope section of the old report is absent, not empty: no endpoint projects
    the active directories or the instruction files
    (see docs/technical/architecture/terminal-client.md).
    """
    budget = context.get('budget') or {}
    cost = context.get('cost') or {}
    lines = ['Context (from the service)']
    lines.append(f'  window           : {_tokens(context.get("window"))}')
    used = context.get('used')
    if used is None:
        lines.append('  used             : the service reported no projection')
    else:
        lines.append(f'  used             : {used:,} ({_percent(context.get("percent"))})')
    lines.append(f'  measured         : {_tokens(context.get("measured"))}')
    lines.append(f'  projected        : {_tokens(context.get("projected"))}')

    lines.append('')
    lines.append('Cost')
    total = cost.get('total')
    if total is None:
        lines.append('  total            : not priced yet')
    else:
        suffix = ' (at list price)' if cost.get('approximate') else ''
        lines.append(f'  total            : {total}{suffix}')
    lines.append(f'  requests         : {_tokens(cost.get("requests"))}')

    lines.append('')
    lines.append('Budget')
    lines.append(f'  tools            : {_tokens(budget.get("tools"))}')
    lines.append(f'  instructions     : {_tokens(budget.get("instructions"))}')
    lines.append(f'  output reserve   : {_tokens(budget.get("output_reserve"))}')
    lines.append(f'  safety reserve   : {_tokens(budget.get("safety_reserve"))}')
    lines.append(f'  available        : {_tokens(budget.get("available_for_history"))}')

    if workspace:
        worktree = workspace.get('worktree') or {}
        lines.append('')
        lines.append('Workspace')
        lines.append(f'  name             : {workspace.get("label") or "(unnamed)"}')
        lines.append(f'  root             : {workspace.get("root") or "(not reported)"}')
        lines.append(f'  branch           : {workspace.get("branch") or "(none)"}')
        if worktree:
            lines.append(f'  worktree         : {"main" if worktree.get("is_main") else "linked"}')
    return '\n'.join(lines)
