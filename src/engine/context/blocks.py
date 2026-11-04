"""Bloques de instrucciones que se inyectan sin importar la conversación.

El bloque `workspace` resuelve `AGENTS.md` (o `CLAUDE.md` en su defecto) sobre el
árbol de directorios activo:

- La raíz de la sesión aplica siempre.
- Un subdirectorio aplica sólo cuando el agente opera dentro de él.
- El orden es padre → hijo: el hijo va último y tiene precedencia.
- En un mismo directorio, `AGENTS.md` gana sobre `CLAUDE.md`.

Es contenido de operador, no del usuario: viaja por el canal de instrucciones,
nunca como mensaje de usuario.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AGENTS_FILENAME = 'AGENTS.md'
FALLBACK_FILENAME = 'CLAUDE.md'

MAX_FILE_CHARS = 32_000
"""Tope por archivo. Un `AGENTS.md` de 40 KB se come la ventana."""

MAX_TOTAL_CHARS = 64_000
"""Tope del bloque completo, sumando todos los niveles del árbol."""

InstructionChangePolicy = Literal['rewrite', 'append']


@dataclass(frozen=True, slots=True)
class InstructionBlock:
    """Un bloque de instrucciones con su política de cambio."""

    name: str
    content: str
    on_change: InstructionChangePolicy = 'rewrite'
    source: str | None = None
    """De dónde salió, para diagnóstico. No se manda al modelo."""


@dataclass(frozen=True, slots=True)
class WorkspaceFile:
    """Un archivo de instrucciones encontrado en el árbol."""

    path: Path
    relative: str
    content: str
    truncated: bool


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    mtime_ns: int
    size: int
    content: str
    truncated: bool


class WorkspaceInstructions:
    """Resuelve los archivos de instrucciones del árbol de directorios."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_file_chars: int = MAX_FILE_CHARS,
        max_total_chars: int = MAX_TOTAL_CHARS,
    ) -> None:
        self._root = Path(os.path.realpath(str(root)))
        self._max_file_chars = max_file_chars
        self._max_total_chars = max_total_chars
        self._cache: dict[Path, _CacheEntry] = {}

    @property
    def root(self) -> Path:
        return self._root

    def _read(self, path: Path) -> _CacheEntry | None:
        """Lee un archivo con cache por (mtime, size)."""
        try:
            stat = path.stat()
        except OSError:
            self._cache.pop(path, None)
            return None

        cached = self._cache.get(path)
        if cached is not None and cached.mtime_ns == stat.st_mtime_ns and cached.size == stat.st_size:
            return cached

        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return None

        truncated = len(text) > self._max_file_chars
        if truncated:
            text = text[: self._max_file_chars]
        entry = _CacheEntry(mtime_ns=stat.st_mtime_ns, size=stat.st_size, content=text, truncated=truncated)
        self._cache[path] = entry
        return entry

    def _file_for(self, directory: Path) -> Path | None:
        """`AGENTS.md` si existe; si no, `CLAUDE.md`."""
        for filename in (AGENTS_FILENAME, FALLBACK_FILENAME):
            candidate = directory / filename
            if candidate.is_file():
                return candidate
        return None

    def resolve(self, scopes: Iterable[Path]) -> list[WorkspaceFile]:
        """Archivos de instrucciones para los directorios dados, padre → hijo."""
        ordered = sorted(set(scopes), key=lambda path: (len(path.parts), str(path)))
        found: list[WorkspaceFile] = []
        total = 0

        for directory in ordered:
            try:
                directory.relative_to(self._root)
            except ValueError:
                continue
            path = self._file_for(directory)
            if path is None:
                continue
            entry = self._read(path)
            if entry is None or not entry.content.strip():
                continue

            remaining = self._max_total_chars - total
            if remaining <= 0:
                break
            content = entry.content
            truncated = entry.truncated
            if len(content) > remaining:
                content = content[:remaining]
                truncated = True
            total += len(content)

            relative = path.relative_to(self._root)
            found.append(
                WorkspaceFile(
                    path=path,
                    relative='.' if str(relative) == '.' else str(relative),
                    content=content,
                    truncated=truncated,
                )
            )
        return found

    def render(self, scopes: Iterable[Path]) -> str:
        """Texto del bloque `workspace`, listo para las instrucciones."""
        files = self.resolve(scopes)
        if not files:
            return ''

        lines = [
            '## Workspace instructions',
            '',
            'The following files were found in the working directory tree. They are',
            'operator-authored project instructions. More specific files (deeper in the',
            'tree) take precedence over more general ones when they conflict.',
            '',
        ]
        for file in files:
            lines.append(f'### `{file.relative}`')
            lines.append('')
            lines.append(file.content.rstrip())
            if file.truncated:
                lines.append('')
                lines.append(f'_[truncated: `{file.relative}` exceeds the size limit]_')
            lines.append('')
        return '\n'.join(lines).rstrip() + '\n'

    def block(self, scopes: Iterable[Path]) -> InstructionBlock | None:
        """El bloque `workspace`, o ``None`` si no hay archivos.

        `on_change='append'`: el scope se amplía a mitad de turno cuando una tool
        entra en un subdirectorio nuevo. Agregar el texto del hijo preserva el
        prefijo de instrucciones; reescribirlo invalidaría el prompt cache.
        """
        content = self.render(scopes)
        if not content:
            return None
        return InstructionBlock(name='workspace', content=content, on_change='append', source='AGENTS.md/CLAUDE.md')

    def describe(self, scopes: Iterable[Path]) -> Sequence[str]:
        """Rutas relativas de los archivos resueltos, para la UI."""
        return [file.relative for file in self.resolve(scopes)]
