"""Índice de skills descubiertos y operaciones de carga/lectura.

El registry es la única pieza que conoce las raíces y el estado del índice. Las
tools y los bloques de instrucciones le piden metadatos, cuerpos y recursos; no
saben de dónde salieron.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from src.engine.skills.discovery import (
    DEFAULT_MAX_READ_BYTES,
    MAX_READ_BYTES,
    default_roots,
    list_resources,
    safe_join,
)
from src.engine.skills.models.skill import Skill
from src.engine.skills.models.skill_metadata import SkillMetadata
from src.engine.skills.models.skill_not_found import SkillNotFound
from src.engine.skills.models.skill_parse_error import SkillParseError
from src.engine.skills.models.skill_resource import SkillResource
from src.engine.skills.models.skill_resource_error import SkillResourceError
from src.engine.skills.parser import parse_skill_metadata, split_frontmatter


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
                # Permitir buscar por nombre de directorio aunque el frontmatter
                # use otro identificador.
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
        return self._skills.get(self._aliases.get(key, key))

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
        return list_resources(metadata)

    def resolve_resource(self, name: str, relative_path: str) -> Path:
        """Resuelve un recurso garantizando que quede dentro del skill."""
        return safe_join(self.require(name), relative_path)

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

        return data[:limit], len(data) > limit, total_size

    # --- diagnóstico ------------------------------------------------------

    def iter_errors(self) -> Iterable[str]:
        return tuple(self.errors)
