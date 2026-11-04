"""Constantes de las estrategias de vista."""

from __future__ import annotations

DEFAULT_KEEP_RECENT_TURNS = 3
"""Turnos que se mantienen intactos antes de empezar a recortar."""

DEFAULT_MAX_TOOL_RESULT_CHARS = 4_000
"""Tope de un tool result viejo. Los recientes no se tocan."""

DEFAULT_TOOL_RESULT_HEAD_CHARS = 400
"""Cuánto se conserva de un tool result truncado, para no perder la señal."""

PINNED_TOOLS: frozenset[str] = frozenset({'load_skill', 'read_skill_resource'})
"""Tools cuyo resultado es **instrucción**, no dato: nunca se truncan ni se compactan.

El cuerpo de un skill es lo que el agente tiene que seguir haciendo. Truncarlo a
un stub o colapsarlo en "load_skill → ok" le saca las instrucciones en medio de
la tarea. Medido: los skills instalados pesan entre 454 y 8.256 tokens.
"""
