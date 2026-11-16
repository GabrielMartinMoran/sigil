# Configuration files and data directories

Sigil separates the project you work on, the checkout that runs the application,
and the directory that stores your personal state. Keep those locations distinct
when diagnosing a setting that doesn't seem to apply.

## Find the right location

Use this map before creating a configuration file:

| Location                         | Purpose                                              |
| -------------------------------- | ---------------------------------------------------- |
| Your launch directory            | Workspace for file tools and project discovery       |
| `<workspace>/.sigil/config.json` | Project MCP servers and model defaults               |
| `<workspace>/.sigil/agents/`     | Project agent profiles                               |
| `<workspace>/.agents/skills/`    | Project Agent Skills                                 |
| `<workspace>/.sigil/tools/`      | Project Python extensions, subject to trust approval |
| `<workspace>/AGENTS.md`          | Project instructions; `CLAUDE.md` is the fallback    |
| `<SIGIL_HOME>/config.json`       | Global MCP servers and model defaults                |
| `<SIGIL_HOME>/agents/`           | Global agent profiles                                |
| `~/.agents/skills/`              | Global Agent Skills; independent of `SIGIL_HOME`     |
| `<SIGIL_HOME>/tools/`            | Trusted global Python extensions                     |
| `<SIGIL_HOME>/themes/`           | Installed themes, unless overridden                  |
| Sigil's checkout                 | Application code, built-ins, and the launcher        |

`SIGIL_HOME` defaults to `~/.sigil`. Create project configuration directories
only when you need them; Sigil can run without them.

Instructions can also come from applicable subdirectories. At each directory,
`AGENTS.md` takes precedence over `CLAUDE.md`; deeper files apply when the agent
works inside that directory and take precedence over more general instructions.
See [Project instructions](../customization/project-instructions.md).

## Choose project or global configuration

Use `<workspace>/.sigil/config.json` for choices specific to one project and
`<SIGIL_HOME>/config.json` for choices that follow you between projects. The
supported sections include `mcp_servers`, `agents`, and `default_model`:

```json
{
  "mcp_servers": {},
  "agents": {
    "scout": { "account": "work", "model": "your-model-id", "effort": "low" }
  },
  "default_model": {
    "account": "work",
    "model": "your-model-id",
    "effort": "default"
  }
}
```

Replace the account alias and model IDs with ones available to you. The project
entry replaces the global entry with the same server or agent name; it isn't a
field-by-field merge within that entry. `SIGIL_CONFIG=/path/config.json` selects
one file instead of both. See [MCP](mcp.md) and [model defaults](models.md) for
complete examples.

For MCP `cwd`, a relative path is resolved against the configuration file's
directory, **not** the workspace. Other discovery roots have their own rules.

## Find saved state

The default data directory contains these important files:

```text
~/.sigil/
├── sigil.db                    # sessions, accounts, plans, and app state
├── providers/
│   └── credentials.json        # provider keys and OAuth tokens
├── ui.json                     # terminal display and alert preferences
├── themes/                     # custom themes
├── worktrees/                  # worktrees created by Sigil
└── service/                    # local endpoint and diagnostics
```

Credentials use private file permissions but aren't stored in an OS keyring.
Protect this directory and its backups. Don't publish credentials or the service
endpoint token when reporting a problem.

`SIGIL_HOME` relocates the base directory. Set it before starting Sigil and keep
it consistent across clients that must share state. It doesn't migrate existing
data automatically. Additional path overrides are in the
[environment reference](../reference/environment.md).

## Understand environment loading

The terminal loads the environment file next to Sigil's code or in a parent of
that checkout, not the environment file of an unrelated workspace. Export
startup settings in your shell when you want them to apply consistently. The
server commands also load startup environment settings; avoid relying on an
unrelated project's environment file for application configuration.

Settings are read at startup. Provider and model choices made through the UI can
change between turns without restarting. For a directory-scope change, follow
the specific guide rather than assuming every setting hot-reloads.

---

[Documentation index](../../README.md) · [User documentation](../README.md)
