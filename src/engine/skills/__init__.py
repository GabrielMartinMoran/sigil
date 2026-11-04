"""Soporte del protocolo Agent Skills para Sigil.

Un skill es un directorio con un ``SKILL.md`` (frontmatter YAML + cuerpo
Markdown) y recursos opcionales (``scripts/``, ``references/``, ``assets/``).

El módulo implementa el protocolo completo de consumo:

- descubrimiento en ``<cwd>/.agents/skills`` y ``~/.agents/skills``,
- *progressive disclosure*: metadatos siempre disponibles, cuerpo bajo demanda,
  recursos y scripts cuando se necesitan,
- lectura segura de recursos (sin escapes de directorio),
- ejecución de scripts empaquetados dentro del skill.

El paquete no re-exporta símbolos: importá del módulo dueño.
"""
