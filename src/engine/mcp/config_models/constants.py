"""Constantes de validación de la configuración de servidores MCP."""

from __future__ import annotations

import re

# El handshake inicial de un server stdio suele incluir un arranque en frío
# (``npx -y``, ``uvx``), así que el default de 5s de la librería queda corto.
DEFAULT_INIT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 300.0

SERVER_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
TOOL_PREFIX_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_]*$')
