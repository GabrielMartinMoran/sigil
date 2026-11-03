"""Descubrimiento y acceso a los skills instalados.

Raíces por defecto (en orden de precedencia):

1. ``<cwd>/.agents/skills``  — skills del proyecto.
2. ``~/.agents/skills``      — skills globales del usuario.

Si existe la variable de entorno ``SIGIL_SKILLS_DIRS`` (lista separada por
``os.pathsep``), se usan esas rutas en su lugar. Un skill del proyecto con el
mismo nombre que uno global gana (sombra al global).
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath

from src.engine.skills.models import (
    Skill,
    SkillMetadata,
    SkillNotFound,
    SkillParseError,
    SkillResource,
    SkillResourceError,
)
from src.engine.skills.parser import parse_skill_metadata, split_frontmatter

SKILLS_DIRNAME = Path('.agents') / 'skills'
ENV_SKILLS_DIRS = 'SIGIL_SKILLS_DIRS'

MAX_LISTED_RESOURCES = 500
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


def default_roots() -> list[tuple[str, Path]]:
    """Devuelve las raíces de skills como ``(origen, ruta)``."""
    env = os.environ.get(ENV_SKILLS_DIRS, '').strip()
    if env:
        roots: list[tuple[str, Path]] = []
        for raw in env.split(os.pathsep):
            raw = raw.strip()
            if raw:
                roots.append(('custom', Path(raw).expanduser()))
        return roots

    return [
        ('project', Path.cwd() / SKILLS_DIRNAME),
        ('global', Path.home() / SKILLS_DIRNAME),
    ]


def _resource_kind(relative: PurePosixPath) -> str:
    if not relative.parts:
        return 'other'
    top = relative.parts[0]
    if top in {'scripts', 'references', 'assets'}:
        return top.removesuffix('s')
    return 'other'


def _is_excluded(relative: PurePosixPath) -> bool:
    if any(part in _EXCLUDED_DIRS for part in relative.parts):
        return True
    name = relative.name
    if name in _EXCLUDED_NAMES or name.startswith('.'):
        return True
    return relative.suffix.lower() in _EXCLUDED_SUFFIXES


class SkillRegistry:
    """Índice de skills descubiertos y operaciones de carga/lectura."""

    def __init__(self, roots: Sequence[tuple[str, Path]] | None = None) -> None:
        self._roots: list[tuple[str, Path]] = [
            (source, Path(path).expanduser()) for source, path in (roots if roots is not None else default_roots())
        ]
        self._skills: dict[str, SkillMetadata] = {}
        self._aliases: dict[str, str] = {}
        self.errors: list[str] = []
        self._discover()

    # --- descubrimiento ---------------------------------------------------

    def _discover(self) -> None:
        for source, root in self._roots:
            if not root.is_dir():
                continue
            try:
                entries = sorted(root.iterdir(), key=lambda path: path.name)
            except OSError as exc:
                self.errors.append(f'Cannot list skills directory {root}: {exc}')
                continue

            for entry in entries:
                skill_file = entry / 'SKILL.md'
                if not entry.is_dir() or not skill_file.is_file():
                    continue
                try:
                    metadata = parse_skill_metadata(skill_file, source=source)
                except SkillParseError as exc:
                    self.errors.append(f'{entry}: {exc}')
                    continue

                if metadata.name in self._skills:
                    # Ya hay un skill con ese nombre en una raíz de mayor
                    # precedencia; el actual queda sombreado.
                    continue

                self._skills[metadata.name] = metadata
                self._aliases.setdefault(metadata.name, metadata.name)
                # Permitir buscar por nombre de directorio aunque el
                # frontmatter use otro identificador.
                self._aliases.setdefault(entry.name, metadata.name)

    def refresh(self) -> SkillRegistry:
        """Vuelve a escanear las raíces (por si se instaló un skill)."""
        self._skills.clear()
        self._aliases.clear()
        self.errors.clear()
        self._discover()
        return self

    # --- consultas --------------------------------------------------------

    @property
    def roots(self) -> tuple[tuple[str, Path], ...]:
        return tuple(self._roots)

    def __len__(self) -> int:
        return len(self._skills)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and self.get(name) is not None

    def list(self) -> list[SkillMetadata]:
        """Todos los skills, ordenados por nombre."""
        return sorted(self._skills.values(), key=lambda skill: skill.name)

    def get(self, name: str) -> SkillMetadata | None:
        """Busca un skill por nombre canónico o por nombre de directorio."""
        if not isinstance(name, str):
            return None
        key = name.strip()
        if not key:
            return None
        canonical = self._aliases.get(key, key)
        return self._skills.get(canonical)

    def require(self, name: str) -> SkillMetadata:
        metadata = self.get(name)
        if metadata is None:
            available = ', '.join(skill.name for skill in self.list()) or 'none'
            raise SkillNotFound(f"Unknown skill '{name}'. Available skills: {available}.")
        return metadata

    # --- nivel 2: cuerpo del SKILL.md ------------------------------------

    def load(self, name: str) -> Skill:
        """Carga el cuerpo del ``SKILL.md`` y lista los recursos del skill."""
        metadata = self.require(name)
        try:
            text = metadata.skill_file.read_text(encoding='utf-8')
        except OSError as exc:
            raise SkillParseError(f'Cannot read {metadata.skill_file}: {exc}') from exc
        _, body = split_frontmatter(text)
        return Skill(metadata=metadata, body=body.strip(), resources=tuple(self.list_resources(metadata)))

    # --- nivel 3: recursos ------------------------------------------------

    def list_resources(self, metadata: SkillMetadata) -> list[SkillResource]:
        """Lista los archivos empaquetados (excluye SKILL.md y artefactos)."""
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
                if relative.as_posix() == 'SKILL.md' or _is_excluded(relative):
                    continue
                try:
                    size = file_path.stat().st_size
                except OSError:
                    continue
                resources.append(SkillResource(path=relative.as_posix(), size=size, kind=_resource_kind(relative)))
                if len(resources) >= MAX_LISTED_RESOURCES:
                    return resources

        return resources

    def resolve_resource(self, name: str, relative_path: str) -> Path:
        """Resuelve un recurso garantizando que quede dentro del skill."""
        metadata = self.require(name)
        return self._safe_join(metadata, relative_path)

    def _safe_join(self, metadata: SkillMetadata, relative_path: str) -> Path:
        if not isinstance(relative_path, str) or not relative_path.strip():
            raise SkillResourceError('Resource path cannot be empty.')

        raw = relative_path.strip().replace('\\', '/')
        relative = PurePosixPath(raw)
        if relative.is_absolute():
            raise SkillResourceError(f'Resource path must be relative to the skill: {relative_path!r}.')
        if any(part == '..' for part in relative.parts):
            raise SkillResourceError(f'Resource path cannot contain `..`: {relative_path!r}.')

        root = metadata.path.resolve()
        target = (root / Path(*relative.parts)).resolve()
        if not target.is_relative_to(root):
            raise SkillResourceError(f'Resource path escapes the skill directory: {relative_path!r}.')
        return target

    def read_resource(
        self,
        name: str,
        relative_path: str,
        *,
        max_bytes: int = DEFAULT_MAX_READ_BYTES,
    ) -> tuple[bytes, bool, int]:
        """Lee un recurso y devuelve ``(bytes, truncado, tamaño_total)``."""
        target = self.resolve_resource(name, relative_path)
        if not target.is_file():
            raise SkillResourceError(f'Resource not found: {relative_path!r}.')

        limit = max(1, min(max_bytes, MAX_READ_BYTES))
        try:
            total_size = target.stat().st_size
            with target.open('rb') as handle:
                data = handle.read(limit + 1)
        except OSError as exc:
            raise SkillResourceError(f'Cannot read resource {relative_path!r}: {exc}') from exc

        truncated = len(data) > limit
        return data[:limit], truncated, total_size

    # --- instrucciones ----------------------------------------------------

    def render_available_skills(self) -> str:
        """Bloque de metadatos para inyectar en las instrucciones del agente."""
        skills = [skill for skill in self.list() if skill.model_invocable]
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
            description = ' '.join(skill.description.split())
            lines.append(f'- `{skill.name}`: {description}')
        return '\n'.join(lines)

    def iter_errors(self) -> Iterable[str]:
        return tuple(self.errors)
