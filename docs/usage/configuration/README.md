# Configure Sigil

Configure everyday choices through the terminal UI. Use configuration files for
repeatable project settings and environment variables for startup settings.

## Choose what to configure

Start with the setting you want to change:

| Task                                       | Guide                                                |
| ------------------------------------------ | ---------------------------------------------------- |
| Add or remove a provider account           | [Providers and accounts](providers.md)               |
| Set the model and reasoning effort         | [Models and defaults](models.md)                     |
| Find config files or relocate Sigil's data | [Files and directories](files-and-directories.md)    |
| Connect an MCP server                      | [MCP configuration](mcp.md)                          |
| Change a startup setting                   | [Environment variables](../reference/environment.md) |

## Understand precedence

The project configuration overrides matching entries in global configuration.
`SIGIL_CONFIG` selects a single configuration file instead of that merge.
Profiles, skills, and data directories have their own discovery rules; the
[files and directories guide](files-and-directories.md) distinguishes them.

For agent instructions, skills, and themes, continue with
[Customization](../customization/README.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
