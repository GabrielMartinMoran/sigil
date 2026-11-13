"""Constants of the Sigil config file."""

from __future__ import annotations

CONFIG_DIRNAME = '.sigil'
CONFIG_FILENAME = 'config.json'
ENV_CONFIG = 'SIGIL_CONFIG'

TOP_LEVEL_KEYS = frozenset({'mcp_servers', 'agents', 'default_model'})

ENV_VAR_RE = r'\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}'
