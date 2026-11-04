"""Working set: los archivos que el agente tocó, derivados del log.

Cuando la compactación colapsa turnos viejos en un resumen, los payloads se
pierden —que es lo que se quiere— pero con ellos se va el rastro de *qué
archivos* estaba tocando el agente. Ese rastro es barato y es lo que evita que
después de compactar vuelva a leer desde cero lo que ya tenía en la mano.

El working set se **deriva del log**, no se mantiene en un estado aparte:

- es determinístico, igual que la vista;
- no puede desincronizarse del historial;
- sobrevive a `/load` sin persistir nada extra.

Sólo cuentan las tools que declaran un path del workspace (`PATH_TOOLS`). `run`
queda afuera: recibe comandos de shell, no paths, y adivinar qué archivos toca
sería inventar información.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart

from src.engine.context.scope import PATH_TOOLS

DEFAULT_WORKING_SET_LIMIT = 20
"""Archivos que se conservan en el resumen. Más que eso deja de ser un resumen."""

READ_TOOLS: frozenset[str] = frozenset({'read_file'})
"""Tools que sólo leen. El resto de `PATH_TOOLS` escribe."""


@dataclass(frozen=True, slots=True)
class TouchedFile:
    """Un archivo tocado por el agente, con el rastro de qué se le hizo."""

    path: str
    tools: tuple[str, ...]
    """Tools que lo tocaron, en orden de primera aparición, sin repetir."""

    @property
    def written(self) -> bool:
        return any(tool not in READ_TOOLS for tool in self.tools)

    def render(self) -> str:
        return f'- `{self.path}` ({", ".join(self.tools)})'


@dataclass(slots=True)
class _Entry:
    path: str
    tools: list[str] = field(default_factory=list)
    order: int = 0
    """Posición de la última vez que se tocó. Más alto es más reciente."""


def _normalize(raw: str) -> str:
    """Normaliza un path para deduplicar sin inventar rutas absolutas."""
    path = raw.strip().replace('\\', '/')
    while path.startswith('./'):
        path = path[2:]
    return path


def _args_mapping(args: Any) -> Mapping[str, Any] | None:
    """Argumentos de un `ToolCallPart` como mapping.

    pydantic-ai deja `args` como dict cuando pudo parsearlo y como string JSON
    cuando el modelo emitió algo que no validó. Un string que no parsea no
    aporta paths: se ignora en vez de romper la vista.
    """
    if isinstance(args, Mapping):
        return args
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except ValueError:
            return None
        return parsed if isinstance(parsed, Mapping) else None
    return None


def touched_files(
    messages: Sequence[ModelMessage],
    *,
    limit: int = DEFAULT_WORKING_SET_LIMIT,
) -> list[TouchedFile]:
    """Archivos tocados por las tools, del más reciente al más viejo.

    El orden es por última interacción: lo que el agente acaba de tocar es lo
    que más probablemente necesite en el paso siguiente. El desempate es el
    orden de primera aparición, para que dos archivos tocados en el mismo
    mensaje queden estables.
    """
    entries: dict[str, _Entry] = {}
    for position, message in enumerate(messages):
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            arg_names = PATH_TOOLS.get(part.tool_name)
            if not arg_names:
                continue
            args = _args_mapping(part.args)
            if args is None:
                continue
            for arg_name in arg_names:
                raw = args.get(arg_name)
                if not isinstance(raw, str) or not raw.strip():
                    continue
                path = _normalize(raw)
                entry = entries.get(path)
                if entry is None:
                    entry = _Entry(path=path, order=position)
                    entries[path] = entry
                entry.order = position
                if part.tool_name not in entry.tools:
                    entry.tools.append(part.tool_name)

    ordered = sorted(entries.values(), key=lambda entry: (-entry.order, entry.path))
    return [TouchedFile(path=entry.path, tools=tuple(entry.tools)) for entry in ordered[:limit]]


def render_working_set(files: Sequence[TouchedFile]) -> str:
    """Texto del working set para el resumen de compactación."""
    if not files:
        return ''
    lines = ['Files touched so far (most recent first):']
    lines.extend(file.render() for file in files)
    return '\n'.join(lines)
