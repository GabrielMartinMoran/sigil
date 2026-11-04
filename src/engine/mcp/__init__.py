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

El paquete no re-exporta símbolos: importá del módulo dueño.
"""
