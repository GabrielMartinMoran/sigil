"""Bloques de instrucciones que el registry de skills aporta al agente."""

from __future__ import annotations

from collections.abc import Sequence

from src.engine.skills.models.skill_error import SkillError
from src.engine.skills.registry import SkillRegistry


def render_available_skills(registry: SkillRegistry) -> str:
    """Bloque de metadatos para inyectar en las instrucciones del agente."""
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


def render_loaded_skills(registry: SkillRegistry, names: Sequence[str]) -> str:
    """Bloque de instrucciones con el cuerpo de los skills cargados.

    El cuerpo del ``SKILL.md`` va acá y no como resultado de tool: así
    sobrevive a la compactación y no compite por el presupuesto del
    historial. Los recursos empaquetados quedan afuera a propósito — se leen
    on demand con ``read_skill_resource``, que es lo que mantiene barato el
    bloque.
    """
    if not names:
        return ''

    sections: list[str] = []
    for name in names:
        try:
            skill = registry.load(name)
        except SkillError:
            continue
        sections.append(f'### `{skill.metadata.name}`\n\n{skill.body.strip()}')
    if not sections:
        return ''

    header = [
        '## Loaded skills',
        '',
        'The following skills were loaded in this session. Their instructions are',
        'authoritative for the tasks they cover. Bundled resources are NOT included:',
        'read them on demand with `read_skill_resource` and run scripts with',
        '`run_skill_script`. Do not call `load_skill` again for a skill listed here.',
        '',
    ]
    return '\n'.join([*header, '\n\n'.join(sections)]).rstrip() + '\n'
