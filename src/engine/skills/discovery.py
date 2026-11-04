"""Descubrimiento de skills y resolución segura de sus recursos.

Raíces por defecto, en orden de precedencia:

1. ``<cwd>/.agents/skills``  — skills del proyecto.
2. ``~/.agents/skills``      — skills globales del usuario.

``SIGIL_SKILLS_DIRS`` (lista separada por ``os.pathsep``) reemplaza ambas. Un
skill del proyecto con el mismo nombre que uno global lo sombrea.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from src.engine.skills.models.skill_metadata import SkillMetadata
from src.engine.skills.models.skill_resource import SkillResource
from src.engine.skills.models.skill_resource_error import SkillResourceError

SKILLS_DIRNAME = Path('.agents') / 'skills'
ENV_SKILLS_DIRS = 'SIGIL_SKILLS_DIRS'

MAX_LISTED_RESOURCES = 500
"""Tope de recursos listados por skill, para no recorrer un árbol infinito."""

DEFAULT_MAX_READ_BYTES = 256 * 1024
MAX_READ_BYTES = 4 * 1024 * 1024

_EXCLUDED_DIRS = {
    '__pycache__',
    '.git',
    '.hg',
    '.svn',
    '.venv',
    'venv',
    'node_modules',
    '.ruff_cache',
    '.pytest_cache',
    '.mypy_cache',
}
_EXCLUDED_SUFFIXES = {'.pyc', '.pyo'}
_EXCLUDED_NAMES = {'.DS_Store', 'Thumbs.db'}

_RESOURCE_KINDS = {'scripts': 'script', 'references': 'reference', 'assets': 'asset'}


def default_roots() -> list[tuple[str, Path]]:
    """Raíces de skills como ``(origen, ruta)``."""
    env = os.environ.get(ENV_SKILLS_DIRS, '').strip()
    if env:
        return [('custom', Path(raw).expanduser()) for raw in env.split(os.pathsep) if raw.strip()]

    return [
        ('project', Path.cwd() / SKILLS_DIRNAME),
        ('global', Path.home() / SKILLS_DIRNAME),
    ]


def resource_kind(relative: PurePosixPath) -> str:
    """Clasifica un recurso por su carpeta de primer nivel."""
    if not relative.parts:
        return 'other'
    return _RESOURCE_KINDS.get(relative.parts[0], 'other')


def is_excluded(relative: PurePosixPath) -> bool:
    """Si un archivo del skill no debe listarse (caché, artefactos, ocultos)."""
    if any(part in _EXCLUDED_DIRS for part in relative.parts):
        return True
    name = relative.name
    if name in _EXCLUDED_NAMES or name.startswith('.'):
        return True
    return relative.suffix.lower() in _EXCLUDED_SUFFIXES


def list_resources(metadata: SkillMetadata) -> list[SkillResource]:
    """Lista los archivos empaquetados (excluye ``SKILL.md`` y artefactos)."""
    resources: list[SkillResource] = []
    root = metadata.path

    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirnames[:] = sorted(name for name in dirnames if name not in _EXCLUDED_DIRS and not name.startswith('.'))

        for filename in sorted(filenames):
            file_path = current_path / filename
            try:
                relative = PurePosixPath(file_path.relative_to(root).as_posix())
            except ValueError:
                continue
            if relative.as_posix() == 'SKILL.md' or is_excluded(relative):
                continue
            try:
                size = file_path.stat().st_size
            except OSError:
                continue
            resources.append(SkillResource(path=relative.as_posix(), size=size, kind=resource_kind(relative)))
            if len(resources) >= MAX_LISTED_RESOURCES:
                return resources

    return resources


def safe_join(metadata: SkillMetadata, relative_path: str) -> Path:
    """Resuelve un recurso garantizando que quede dentro del skill."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise SkillResourceError('Resource path cannot be empty.')

    relative = PurePosixPath(relative_path.strip().replace('\\', '/'))
    if relative.is_absolute():
        raise SkillResourceError(f'Resource path must be relative to the skill: {relative_path!r}.')
    if any(part == '..' for part in relative.parts):
        raise SkillResourceError(f'Resource path cannot contain `..`: {relative_path!r}.')

    root = metadata.path.resolve()
    target = (root / Path(*relative.parts)).resolve()
    if not target.is_relative_to(root):
        raise SkillResourceError(f'Resource path escapes the skill directory: {relative_path!r}.')
    return target
