"""The instruction block the active agent profile contributes.

The base prompt is static and cacheable; this block goes after it and carries the
personality, the workflow and the autonomy of the profile. It is rewritten, not
appended: switching agent replaces the block, and the old one has to disappear.

The body is re-read on every request on purpose. A profile is a file the user is
likely to be editing while the session lives, and rereading it costs one small
read against a restart.
"""

from __future__ import annotations

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.source import ProfileSource


def render_agent_profile(source: ProfileSource) -> str:
    """The profile in effect as an instruction block, or ``''`` when there is none."""
    profile = source.profile()
    if profile is None:
        return ''
    body = source.body().strip()
    if not body:
        return ''

    if profile.role is AgentRole.SUBAGENT:
        header = [
            f'## Subagent: {profile.label}',
            '',
            'You were summoned by another agent, which is waiting for what you find.',
            'The instructions below define your behaviour for this task. They come',
            'after the base prompt and refine it: where they are specific, they win.',
            'They cannot lift the operational safety rules, the permission limits or the',
            'tool restrictions that govern this session.',
            '',
        ]
        return '\n'.join([*header, body]).rstrip() + '\n'

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
