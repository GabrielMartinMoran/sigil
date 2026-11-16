# MCP (Model Context Protocol)

Connect an [MCP](https://github.com/modelcontextprotocol) server to give your
agent tools from another service. Sigil supports local stdio processes and HTTP
servers, including Streamable HTTP and SSE. Their resources and prompts remain
available through lookup tools.

## Where it is configured

Add MCP settings to `.sigil/config.json`, using **snake_case** keys. Sigil reads
the project and global files; a matching project server replaces the global one:

1. `<cwd>/.sigil/config.json`
2. `<SIGIL_HOME>/config.json` (`~/.sigil/config.json` by default)

`SIGIL_CONFIG=/path/config.json` replaces both. The merge is per server: a
project entry with the same name replaces the global one entirely. The same file
also carries `agents` (a model per profile) and `default_model` (the model a
conversation starts on); see
[Model and effort per agent](models.md#set-defaults-in-configuration).

## Minimal example

Add an entry under `mcp_servers` in the configuration file you chose. This
example uses an HTTP endpoint:

```json
{
  "mcp_servers": {
    "remote": {
      "url": "https://your-mcp-server.example/mcp",
      "headers": { "Authorization": "Bearer ${MCP_TOKEN}" }
    }
  }
}
```

Replace the URL with your server's endpoint and export `MCP_TOKEN` before
starting Sigil. If your server doesn't require a token, omit `headers`. The
server's tools appear as `remote_<tool>` by default. Open `/mcp` to check that
it connected. See the
[example configuration](../../../.sigil/config.example.json) for additional
server entries.

A local server instead needs `command` and any `args`; install its executable
and dependencies separately. Sigil doesn't install a server just because it is
listed in configuration.

## Fields

Choose either a stdio process or a URL-based transport, then supply the fields
that apply to it.

| Key                    | Type                | Applies to | Default              | What it does                                    |
| ---------------------- | ------------------- | ---------- | -------------------- | ----------------------------------------------- |
| `command`              | string              | stdio      | —                    | Server executable                               |
| `args`                 | string[]            | stdio      | `[]`                 | Executable arguments                            |
| `env`                  | object              | stdio      | `{}`                 | Process environment variables                   |
| `cwd`                  | string              | stdio      | inherits Sigil's cwd | Process working directory                       |
| `keep_alive`           | bool                | stdio      | `true`               | Keeps the process alive between turns           |
| `url`                  | string              | HTTP       | —                    | Server endpoint                                 |
| `headers`              | object              | HTTP       | `{}`                 | Headers for every request                       |
| `transport`            | `"http"` \| `"sse"` | HTTP       | inferred             | Forces the transport                            |
| `enabled`              | bool                | both       | `true`               | Turns the server off without deleting it        |
| `prefix`               | string              | both       | server name          | Tool prefix; `""` disables it                   |
| `include_instructions` | bool                | both       | `true`               | Injects the server instructions into the prompt |
| `init_timeout`         | number              | both       | `30`                 | Seconds to connect and perform the handshake    |
| `read_timeout`         | number              | both       | `300`                | Seconds to wait for each message                |
| `include_tools`        | string[]            | both       | `[]`                 | Tool allowlist                                  |
| `exclude_tools`        | string[]            | both       | `[]`                 | Tool denylist                                   |

Each server declares `command` (stdio) or `url` (HTTP), never both. Fields of
one transport on the other are an error, not ignored. `include_tools` and
`exclude_tools` are also mutually exclusive.

## Environment variables in the file

Use `${VAR}` to read a variable and `${VAR:-default}` to give it a fallback. It
works in any string value, including `cwd`:

```json
{
  "mcp_servers": {
    "remote": {
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer ${MCP_TOKEN:-}" }
    }
  }
}
```

A variable without a default and without a value is a config error, not a silent
empty string.

## `cwd`: where the server works

`cwd` only applies to stdio servers. Without `cwd` the process inherits Sigil's
cwd, which is the right thing for servers that operate on the project
(filesystem, git).

A relative path is resolved against the **config file's directory**, not against
Sigil's cwd: `"cwd": ".mcp/playwright"` in `.sigil/config.json` points to
`.sigil/.mcp/playwright` no matter where you start from. If the directory does
not exist, that server is marked as failed with the path in the message; Sigil
does not create directories for you.

For Playwright, `cwd` is not the knob for screenshots: that is controlled with
`--output-dir`.

```json
{
  "mcp_servers": {
    "playwright": {
      "command": "npx",
      "args": [
        "-y",
        "@playwright/mcp@latest",
        "--output-dir",
        "/tmp/sigil-shots"
      ],
      "cwd": "/tmp"
    }
  }
}
```

## Resources and prompts

They are not tools, so they are exposed through four meta-tools:

| Tool                                           | What it returns                             |
| ---------------------------------------------- | ------------------------------------------- |
| `list_mcp_resources(server=None)`              | Published resources, with URI and mime type |
| `read_mcp_resource(server, uri)`               | Content: UTF-8, or base64 if binary         |
| `list_mcp_prompts(server=None)`                | Available prompts and their arguments       |
| `get_mcp_prompt(server, name, arguments=None)` | The already rendered prompt                 |

Plus `list_mcp_servers(connect=False)`, which reports what is configured, what
failed and, with `connect=true`, which tools each server exposes.

With no configured servers, these tools are not registered: they do not spend
context.

## `/mcp` command

`/mcp` opens a scrollable list of servers, their transport, and their state. Use
the arrow keys and Enter to enable or disable a server for the current session.
Your choice is saved with the session; a new session starts with the configured
state. Disabled servers do not expose tools, resources, or prompts to the agent.
Config errors also show up as a notice on startup, without blocking the session.

## What happens if a server fails

A server that cannot connect does not break the turn: it contributes zero tools,
the rest of the conversation keeps going and the error stays visible in `/mcp`.
Parsing errors are reported per server, so a broken entry does not invalidate
the rest.

## Limitations

Sampling, elicitation and roots are not announced to the server. They require
the client to lend the model or the UI mid-turn, and that opens recursion and
interactive questions inside a tool. A server that depends on those capabilities
will fail when asking for them.

---

[Documentation index](../../README.md) · [User documentation](../README.md)
