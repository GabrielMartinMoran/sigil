"""How a tool describes itself in one line.

The summary next to a call in the chat is **domain knowledge**: only `patch_file`
knows that "3 replacements" is the interesting fact about its result, and that
`bytes_before` and `bytes_after` are not. That is why the summary is declared next
to the tool — the same reasoning `permission.py` gives for its group vocabulary —
and not in a `switch` over tool names inside the UI.

Two things cross the line, both of them data:

- **the text**, as a short plain string;
- **how it went**, as a status: `good`, `bad` or `neutral`.

Nothing about layout crosses it: no colors, no widths, no glyphs. The clients
combine the call outcome with the status declared here to paint the row. A failed
test is a bad result, not a raised exception; the detail preserves that distinction
even when both receive a failure mark.

A tool that declares no presenter gets no summary. That is deliberate: the fallback
of the old code was `str(result)`, which put a Pydantic repr in the middle of the
conversation.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

Status = Literal['good', 'bad', 'neutral']
"""How the call went, from the tool's point of view."""

SUMMARY_LIMIT = 120
"""Characters kept in the one-line summary. Wider than a phone, narrower than a row."""

MAX_HUMAN_BYTES = 1024
"""Above this, a size is shown in KB: `840 B` says more than `0.8 KB`."""


@dataclass(frozen=True, slots=True)
class ToolSummary:
    """What a tool says about a finished call, and how it went.

    It travels in the tool event and the UI paints it, so it is a value object and
    not a renderable: a presenter that returned a Rich `Text` would be deciding
    colors from the engine.
    """

    text: str
    status: Status = 'neutral'


Describe = Callable[[Mapping[str, Any], Any], ToolSummary | None]
"""Reads the effective args of a call and its result, and summarizes the result."""


def summary_of(describe: Describe | None, args: Mapping[str, Any], result: Any) -> ToolSummary | None:
    """The summary ``describe`` declared, or ``None``.

    It never raises and never propagates: logging a tool must not break the tool, and
    a presenter that trips over an unexpected result is a reason to show nothing, not
    a reason to fail a call the model is waiting for.
    """
    if describe is None:
        return None
    try:
        summary = describe(args, result)
    except Exception:  # noqa: BLE001 - a bad presenter must not break a tool
        return None
    return _normalized(summary)


def _normalized(summary: ToolSummary | None) -> ToolSummary | None:
    """One line, trimmed, clipped. Empty means no summary at all."""
    if summary is None:
        return None
    text = ' '.join(str(summary.text).split())
    if not text:
        return None
    if len(text) > SUMMARY_LIMIT:
        text = text[: SUMMARY_LIMIT - 1] + '…'
    return ToolSummary(text=text, status=summary.status)


def human_bytes(size: int) -> str:
    """``840 B``, ``1.2 KB``: the unit grows, the number stays short."""
    if size < MAX_HUMAN_BYTES:
        return f'{size} B'
    kilobytes = size / MAX_HUMAN_BYTES
    if kilobytes < MAX_HUMAN_BYTES:
        return f'{kilobytes:.1f} KB'
    return f'{kilobytes / MAX_HUMAN_BYTES:.1f} MB'


DISPLAY_NAMES: Mapping[str, str] = {
    'run': 'run',
    'read_file': 'read',
    'write_file': 'write',
    'patch_file': 'patch',
    'web_search': 'search',
    'read_url': 'fetch',
    'list_skills': 'skills',
    'load_skill': 'skill',
    'read_skill_resource': 'skill',
    'run_skill_script': 'skill',
    'list_mcp_servers': 'mcp',
    'list_mcp_resources': 'mcp',
    'read_mcp_resource': 'mcp',
    'list_mcp_prompts': 'mcp',
    'get_mcp_prompt': 'mcp',
    'ask_user': 'ask',
    'update_roadmap': 'plan',
    'summon': 'spawn',
    'send_message': 'tell',
    'ack_notifications': 'ack',
    'kill_subagent': 'stop',
    'list_agents': 'agents',
}
"""The label a built-in tool shows in the transcript.

Short on purpose: a row shares its width with the arguments and the result, and
`write_file` costs six columns to say what `write` says. A name that is not here —
every MCP tool — is shown as it is, which is also what makes an unfamiliar tool
visible as unfamiliar.
"""


def display_name(tool_name: str) -> str:
    """The label for ``tool_name``, or the name itself when it has none."""
    return DISPLAY_NAMES.get(tool_name, tool_name)
