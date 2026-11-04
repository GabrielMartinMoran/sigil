"""Skill discovery and safe resolution of their resources.

Default roots, in order of precedence:

1. ``<cwd>/.agents/skills``: project skills.
2. ``~/.agents/skills``: user global skills.

``SIGIL_SKILLS_DIRS`` (a list separated by ``os.pathsep``) replaces both. A
project skill with the same name as a global one shadows it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from src.engine.skills.models.skill_metadata import SkillMetadata
from src.engine.skills.models.skill_resource import SkillResource
from src.engine.skills.models.skill_resource_error import SkillResourceError

SKILLS_DIRNAME = Path('.agents') / 'skills'
ENV_SKILLS_DIRS = 'SIGIL_SKILLS_DIRS'

MAX_LISTED_RESOURCES = 500
"""Cap of resources listed per skill, to avoid walking an infinite tree."""

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
    """Skill roots as ``(source, path)``."""
    env = os.environ.get(ENV_SKILLS_DIRS, '').strip()
    if env:
        return [('custom', Path(raw).expanduser()) for raw in env.split(os.pathsep) if raw.strip()]

    return [
        ('project', Path.cwd() / SKILLS_DIRNAME),
        ('global', Path.home() / SKILLS_DIRNAME),
    ]


def roots_are_workspace_scoped(
    roots: Sequence[tuple[str, Path]] | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Whether the roots follow the workspace, so they can be re-pointed on a switch.

    Only the default roots do: an explicit ``SIGIL_SKILLS_DIRS`` names directories
    that belong to the user, not to the directory Sigil happens to be working in.

    Args:
        roots: Explicit roots, if the caller passed any.
        environ: Environment to read ``SIGIL_SKILLS_DIRS`` from (``os.environ``
            by default). Passing it explicitly keeps callers testable.
    """
    if roots is not None:
        return False
    env = os.environ if environ is None else environ
    return not env.get(ENV_SKILLS_DIRS, '').strip()


def resource_kind(relative: PurePosixPath) -> str:
    """Classifies a resource by its first-level folder."""
    if not relative.parts:
        return 'other'
    return _RESOURCE_KINDS.get(relative.parts[0], 'other')


def is_excluded(relative: PurePosixPath) -> bool:
    """Whether a skill file must not be listed (cache, artifacts, hidden)."""
    if any(part in _EXCLUDED_DIRS for part in relative.parts):
        return True
    name = relative.name
    if name in _EXCLUDED_NAMES or name.startswith('.'):
        return True
    return relative.suffix.lower() in _EXCLUDED_SUFFIXES


def list_resources(metadata: SkillMetadata) -> list[SkillResource]:
    """Lists the bundled files (excludes ``SKILL.md`` and artifacts)."""
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
    """Resolves a resource guaranteeing it stays inside the skill."""
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
