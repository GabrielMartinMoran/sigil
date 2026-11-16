# Customize Sigil

Extend the workflow without changing Sigil's core. Choose the mechanism that
matches what you want to add; each has its own installation location.

## Pick an extension point

Use instructions for behavior, skills for reusable expertise, and tools for new
capabilities:

| I want to…                                         | Use                                             |
| -------------------------------------------------- | ----------------------------------------------- |
| Change the agent's workflow or allowed tools       | [Agent profiles](agents.md)                     |
| Give the agent repository-specific rules           | [Project instructions](project-instructions.md) |
| Add reusable instructions and supporting resources | [Agent Skills](skills.md)                       |
| Connect tools from another service                 | [MCP servers](../configuration/mcp.md)          |
| Load a local Python tool extension                 | [Local tools](local-tools.md)                   |
| Change colors or make a theme                      | [Themes](themes.md)                             |

## Trust extensions deliberately

Skills can include executable scripts. MCP servers and Python extensions can
access external services and local resources. Install only code you trust;
[tool permissions are not a security sandbox](../guides/safety.md).

To write a Python extension, continue with the
[extension author guide](../../technical/development/local-tools.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
