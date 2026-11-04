"""Guarda de concurrencia para las tools que escriben archivos.

Cada llamada lee el archivo, calcula el resultado y lo escribe. Si dos corren a
la vez sobre el mismo archivo, la última en escribir borra el trabajo de la
otra. La primera del lote pasa; las siguientes fallan con un mensaje que le dice
al modelo cómo seguir.
"""

from __future__ import annotations

from src.engine.files.models import FileToolError
from src.engine.files.paths import resolve
from src.engine.tooling.concurrency import ConcurrencyContext

MUTATING_TOOLS = frozenset({'patch_file', 'write_file'})
"""Tools que reescriben un archivo y por lo tanto no pueden solaparse."""


def _target_of(args: dict[str, object]) -> object | None:
    """Ruta resuelta de una llamada, o ``None`` si no se puede resolver."""
    try:
        return resolve(str(args['path']))
    except FileToolError, KeyError:
        # Ruta inválida o ausente: la tool fallará con un mensaje mejor.
        return None


def same_file_conflict(ctx: ConcurrencyContext) -> str | None:
    """Dos escrituras al mismo archivo en una misma respuesta se pisarían."""
    target = _target_of(ctx.args)
    if target is None:
        return None

    for sibling in ctx.siblings:
        if sibling.tool_name not in MUTATING_TOOLS:
            continue
        if _target_of(sibling.args) == target:
            return (
                f'Another {sibling.tool_name} call in this same response already edits {str(target)!r}. '
                'Concurrent edits to one file overwrite each other, so this call was not applied. '
                'Re-issue it in a separate step, after the earlier edit lands.'
            )
    return None
