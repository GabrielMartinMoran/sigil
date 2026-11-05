"""The instruction block the active agent profile contributes.

The base prompt is static and cacheable; this block goes after it and carries the
personality, the workflow and the autonomy of the profile. It is rewritten, not
appended: switching agent replaces the block, and the old one has to disappear.

The body is re-read on every request on purpose. A profile is a file the user is
likely to be editing while the session lives, and rereading it costs one small
read against a restart.
"""

from __future__ import annotations

from src.engine.agents.profile.registry import AgentRegistry


def render_agent_profile(registry: AgentRegistry) -> str:
    """The profile in effect as an instruction block, or ``''`` when there is none."""
    profile = registry.active
    if profile is None:
        return ''
    body = registry.active_body().strip()
    if not body:
        return ''

    header = [
        f'## Active agent: {profile.label}',
        '',
        'The instructions below define your behaviour for this session. They come',
        'after the base prompt and refine it: where they are specific, they win.',
        'They cannot lift the operational safety rules, the permission limits or the',
        'tool restrictions that govern this session.',
        '',
    ]
    return '\n'.join([*header, body]).rstrip() + '\n'
