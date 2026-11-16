"""Model Context Protocol (MCP) support for Sigil.

Servers are declared in ``.sigil/config.json`` (or ``~/.sigil/config.json``)
under the ``mcp_servers`` key, with snake_case keys::

    {
      "mcp_servers": {
        "github": {
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-github"],
          "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
        }
      }
    }

The module covers:

- config discovery, merge and validation,
- stdio and HTTP transports (Streamable HTTP and SSE),
- each server's tools as agent toolsets, with a per-server prefix,
- resources and prompts as meta-tools,
- controlled degradation when a server does not respond.

The package re-exports no symbols: import from the owning module.
"""
