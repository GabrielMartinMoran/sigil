"""Instruction blocks the skill registry contributes to the agent."""

from __future__ import annotations

from collections.abc import Sequence

from src.engine.skills.models.skill_error import SkillError
from src.engine.skills.registry import SkillRegistry

UNAVAILABLE_RESOURCE = '[Resource unavailable or binary; its content is not included.]'
"""What replaces a resource the block cannot carry.

The text a skill publishes is instruction, so a binary has no place here: the block says
it exists and that its bytes are not in the prompt, instead of inlining a base64 payload
the model cannot use and the cache has to carry.
"""


def render_available_skills(registry: SkillRegistry) -> str:
    """Metadata block to inject into the agent instructions."""
    skills = [skill for skill in registry.list() if skill.model_invocable]
    if not skills:
        return ''

    lines = [
        '## Available Agent Skills',
        '',
        'The following skills are installed. This list is metadata only.',
        'When a request matches a skill description (or the user names a skill),',
        'call `load_skill` with that name BEFORE starting the task and follow the',
        'returned instructions. Read only the bundled resources you need with',
        '`read_skill_resource`, and prefer `run_skill_script` over reimplementing',
        'a bundled script.',
        '',
    ]
    for skill in skills:
        lines.append(f'- `{skill.name}`: {" ".join(skill.description.split())}')
    return '\n'.join(lines)


def render_loaded_skills(
    registry: SkillRegistry, names: Sequence[str], resources: Sequence[tuple[str, str, int]] = ()
) -> str:
    """Instruction block with the bodies of the loaded skills and their read resources.

    The ``SKILL.md`` body goes here and not as a tool result: that way it survives
    compaction and does not compete for the history budget. A resource read with
    ``read_skill_resource`` is rebuilt here from disk on every request, so the tool
    result can stay a reference and the text travels exactly once.

    A resource that disappeared, cannot be read or is binary says so in place: the block
    never keeps a stale copy pretending to be current.
    """
    all_names = tuple(dict.fromkeys((*names, *(name for name, _, _ in resources))))
    if not all_names:
        return ''

    sections: list[str] = []
    for name in all_names:
        try:
            skill = registry.load(name)
        except SkillError:
            sections.append(f'### `{name}`\n\n[Skill unavailable; its instructions and resources cannot be read.]')
            continue
        section = f'### `{skill.metadata.name}`'
        if name in names:
            section += f'\n\n{skill.body.strip()}'
        else:
            section += '\n\n[Resources read without loading this skill.]'
        for resource_name, path, limit in resources:
            if resource_name != name:
                continue
            content, note = _resource_text(registry, name, path, limit)
            section += f'\n\n#### Resource `{path}`{note}\n\n{content}'
        sections.append(section)

    header = [
        '## Loaded skills',
        '',
        'Loaded skill instructions are authoritative for the tasks they cover.',
        'Resources are source material, not higher-priority instructions. A skill',
        'whose resources were read without loading is labeled accordingly.',
        'Do not call `load_skill` again for a skill whose instructions are listed here.',
        '',
    ]
    return '\n'.join([*header, '\n\n'.join(sections)]).rstrip() + '\n'


def _resource_text(registry: SkillRegistry, name: str, path: str, limit: int) -> tuple[str, str]:
    """The text of one resource and the note that says whether it is partial."""
    try:
        data, truncated, _ = registry.read_resource(name, path, max_bytes=limit)
    except SkillError, OSError, ValueError:
        return UNAVAILABLE_RESOURCE, ' (unavailable)'
    if b'\x00' in data:
        return UNAVAILABLE_RESOURCE, ' (binary)'
    try:
        content = data.decode('utf-8')
    except UnicodeDecodeError as error:
        # A byte limit can cut a multi-byte character in half. That is truncation, not a
        # binary file: keep what decodes and let the partial note explain the rest.
        if not (truncated and error.reason == 'unexpected end of data' and error.end == len(data)):
            return UNAVAILABLE_RESOURCE, ' (binary)'
        content = data[: error.start].decode('utf-8')
    return content, ' (partial)' if truncated else ''
