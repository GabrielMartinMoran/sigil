# Install local tool extensions

Local extensions add Python tools without modifying Sigil's core or running an
MCP server. Use this mechanism for trusted local code; use
[MCP](../configuration/mcp.md) when connecting an external tool server.

## Put the extension in the right place

Each extension is an immediate subdirectory with a `tools.py` entry point:

```text
.sigil/tools/my-extension/tools.py
```

Use the workspace's `.sigil/tools/` for a project extension, or
`<SIGIL_HOME>/tools/` for one available globally. Global extensions are treated
as trusted installed code. Only functions decorated with `@tool` from
`sigil_tools` become agent tools; tools and lifecycle hooks must use
`async def`.

## Approve project code deliberately

Before the first model request, Sigil asks whether to load the project's Python
extensions. Inspect the code before accepting. Declining or cancelling leaves
project extensions unloaded. A client without an interactive question handler
doesn't trust project extensions automatically.

These are trusted-code extensions, not sandboxed plugins. Python import, hooks,
and tool bodies run in Sigil's process with your user permissions. The extension
owns external dependencies and resource cleanup; Sigil doesn't install its
dependencies automatically.

## Check an unavailable tool

Confirm the directory has `tools.py`, that you approved trust for project code,
and that exported tools are decorated asynchronous functions. A load or schema
error is reported without disabling unrelated extensions.

Project tools can override global tools of the same name, with a diagnostic.
Extensions cannot replace registered built-in tools. Tool names come from the
function or decorator, not automatically from the directory name.

## Write an extension

Continue with the
[extension author guide](../../technical/development/local-tools.md) for a
working example, `ToolContext`, lifecycle hooks, concurrency, permissions, and
compaction callbacks. Review [Safety](../guides/safety.md) before enabling code
that can access files, processes, or external services.

---

[Documentation index](../../README.md) · [User documentation](../README.md)
