"""Nombres y límites de los archivos de instrucciones del workspace."""

from __future__ import annotations

from typing import Literal

AGENTS_FILENAME = 'AGENTS.md'
FALLBACK_FILENAME = 'CLAUDE.md'

MAX_FILE_CHARS = 32_000
"""Tope por archivo. Un `AGENTS.md` de 40 KB se come la ventana."""

MAX_TOTAL_CHARS = 64_000
"""Tope del bloque completo, sumando todos los niveles del árbol."""

InstructionChangePolicy = Literal['rewrite', 'append']
