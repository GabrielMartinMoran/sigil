"""Tracker de los directorios en los que el agente está operando.

El `AGENTS.md` de un subdirectorio aplica sólo cuando el agente trabaja dentro de
ese subdirectorio. Para saber qué instrucciones corresponden hay que observar los
paths que tocan las tools y quedarse con la cadena de ancestros de cada uno.

La raíz siempre está en el scope: su `AGENTS.md` aplica a toda la sesión.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

PATH_TOOLS: Mapping[str, tuple[str, ...]] = {
    'read_file': ('path',),
    'write_file': ('path',),
    'patch_file': ('path',),
}
"""Tools que declaran un path del workspace y por lo tanto amplían el scope.

`run` queda afuera a propósito: recibe comandos de shell, no paths, y adivinar
qué directorios toca sería inventar información.
"""


class ScopeTracker:
    """Conjunto de directorios activos, con la raíz siempre presente."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(os.path.realpath(str(root)))
        self._active: set[Path] = {self._root}

    @property
    def root(self) -> Path:
        return self._root

    def reset(self) -> None:
        """Vuelve al scope mínimo: sólo la raíz."""
        self._active = {self._root}

    def observe(self, tool_name: str, args: Mapping[str, object] | None) -> bool:
        """Registra los paths de una llamada. Devuelve ``True`` si el scope cambió."""
        if not args:
            return False
        arg_names = PATH_TOOLS.get(tool_name)
        if not arg_names:
            return False

        before = len(self._active)
        for arg_name in arg_names:
            raw = args.get(arg_name)
            if isinstance(raw, str) and raw.strip():
                self._add(raw)
        return len(self._active) != before

    def _add(self, raw_path: str) -> None:
        """Agrega el directorio del path y toda su cadena de ancestros hasta la raíz."""
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        try:
            resolved = Path(os.path.realpath(str(candidate)))
            resolved.relative_to(self._root)
        except OSError, ValueError:
            # Fuera del workspace: no aporta instrucciones de este árbol.
            return

        # Un path puede ser un directorio que todavía no existe (write_file crea
        # padres). Se usa el path tal cual si no existe, y su padre si es archivo.
        directory = resolved if resolved.is_dir() else resolved.parent
        while True:
            self._active.add(directory)
            if directory == self._root:
                break
            parent = directory.parent
            if parent == directory:
                break
            directory = parent

    def scopes(self) -> tuple[Path, ...]:
        """Directorios activos, de la raíz hacia abajo (el hijo tiene precedencia)."""
        return tuple(sorted(self._active, key=lambda path: (len(path.parts), str(path))))

    def relative_scopes(self) -> tuple[str, ...]:
        """Los scopes como rutas relativas a la raíz, para mostrar en la UI."""
        result = []
        for path in self.scopes():
            relative = path.relative_to(self._root)
            result.append('.' if str(relative) == '.' else str(relative))
        return tuple(result)

    def observe_many(self, calls: Iterable[tuple[str, Mapping[str, object] | None]]) -> bool:
        changed = False
        for tool_name, args in calls:
            changed = self.observe(tool_name, args) or changed
        return changed


class ScopeSink:
    """Sink de `ToolLoggerHub` que alimenta el tracker con cada llamada.

    Se engancha al hub en vez de a cada tool: así también ve las tools de MCP y
    las que se registren después, sin tocar sus firmas.
    """

    def __init__(self, tracker: ScopeTracker) -> None:
        self._tracker = tracker

    def __call__(self, event: object) -> None:
        if getattr(event, 'phase', None) != 'start':
            return
        tool_name = getattr(event, 'tool_name', '')
        args = getattr(event, 'args', None)
        if isinstance(tool_name, str) and isinstance(args, Mapping):
            self._tracker.observe(tool_name, args)
