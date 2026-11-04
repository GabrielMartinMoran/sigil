"""Soporte del Model Context Protocol (MCP) para Sigil.

Los servidores se declaran en ``.sigil/config.json`` (o ``~/.sigil/config.json``)
bajo la clave ``mcp_servers``, con claves en snake_case::

    {
      "mcp_servers": {
        "github": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-github"],
          "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
        }
      }
    }

El módulo cubre:

- descubrimiento, merge y validación de la configuración,
- transportes stdio y HTTP (Streamable HTTP y SSE),
- tools de cada servidor como toolsets del agente, con prefijo por servidor,
- resources y prompts como meta-tools,
- degradación controlada cuando un servidor no responde.
"""

from src.engine.mcp.config_loader import (
    CONFIG_DIRNAME,
    CONFIG_FILENAME,
    ENV_CONFIG,
    LoadedConfig,
    config_paths,
    expand_env_vars,
    load_config,
)
from src.engine.mcp.config_models import (
    DEFAULT_INIT_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    MCPConfigError,
    MCPServerConfig,
    MCPServerSpec,
)
from src.engine.mcp.manager import MCPManager
from src.engine.mcp.resilient import ResilientToolset
from src.engine.mcp.server import MCPServer
from src.engine.mcp.tool_models import (
    PromptArgumentInfo,
    PromptInfo,
    PromptMessageInfo,
    PromptResultInfo,
    ResourceContent,
    ResourceInfo,
    ServerStatus,
)
from src.engine.mcp.tools import register_mcp_tools

__all__ = [
    'CONFIG_DIRNAME',
    'CONFIG_FILENAME',
    'DEFAULT_INIT_TIMEOUT',
    'DEFAULT_READ_TIMEOUT',
    'ENV_CONFIG',
    'LoadedConfig',
    'MCPConfigError',
    'MCPManager',
    'MCPServer',
    'MCPServerConfig',
    'MCPServerSpec',
    'PromptArgumentInfo',
    'PromptInfo',
    'PromptMessageInfo',
    'PromptResultInfo',
    'ResilientToolset',
    'ResourceContent',
    'ResourceInfo',
    'ServerStatus',
    'config_paths',
    'expand_env_vars',
    'load_config',
    'register_mcp_tools',
]
