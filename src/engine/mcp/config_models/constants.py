"""Validation constants for the MCP server config."""

from __future__ import annotations

import re

# The initial handshake of a stdio server usually includes a cold start
# (``npx -y``, ``uvx``), so the library's 5s default is too short.
DEFAULT_INIT_TIMEOUT = 30.0
DEFAULT_READ_TIMEOUT = 300.0

SERVER_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]*$')
TOOL_PREFIX_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_]*$')
