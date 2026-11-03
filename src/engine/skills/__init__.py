"""Soporte del protocolo Agent Skills para Sigil.

Un skill es un directorio con un ``SKILL.md`` (frontmatter YAML + cuerpo
Markdown) y recursos opcionales (``scripts/``, ``references/``, ``assets/``).

El módulo implementa el protocolo completo de consumo:

- descubrimiento en ``<cwd>/.agents/skills`` y ``~/.agents/skills``,
- *progressive disclosure*: metadatos siempre disponibles, cuerpo bajo demanda,
  recursos y scripts cuando se necesitan,
- lectura segura de recursos (sin escapes de directorio),
- ejecución de scripts empaquetados dentro del skill.
"""

from src.engine.skills.models import (
    Skill,
    SkillError,
    SkillMetadata,
    SkillNotFound,
    SkillParseError,
    SkillResource,
    SkillResourceError,
)
from src.engine.skills.parser import (
    build_metadata,
    parse_frontmatter,
    parse_skill_metadata,
    split_frontmatter,
)
from src.engine.skills.registry import SkillRegistry, default_roots

__all__ = [
    'Skill',
    'SkillError',
    'SkillMetadata',
    'SkillNotFound',
    'SkillParseError',
    'SkillRegistry',
    'SkillResource',
    'SkillResourceError',
    'build_metadata',
    'default_roots',
    'parse_frontmatter',
    'parse_skill_metadata',
    'split_frontmatter',
]
