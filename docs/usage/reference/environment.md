# Environment variables

Set startup variables before launching Sigil. For ordinary account and model
changes, use `/provider`, `/model`, and `/default-models` instead. The
[configuration guide](../configuration/files-and-directories.md) explains where
files are read and how project settings override global entries.

## Execution and context policy

These variables control the turn budget and the derived model view. They don't
set provider credentials or impose a billing cap.

| Variable                                | Default       | What it does                                                                                                     |
| --------------------------------------- | ------------- | ---------------------------------------------------------------------------------------------------------------- |
| `SIGIL_MAX_STEPS`                       | `256`         | Model calls per turn when the profile declares no `max_steps`; running out ends it with a last tool-less answer. |
| `SIGIL_MODEL_MAX_ATTEMPTS`              | `5`           | Attempts per model request on transient failures.                                                                |
| `SIGIL_CONTEXT_STRATEGY`                | `append-only` | `append-only` or `passthrough`.                                                                                  |
| `SIGIL_CONTEXT_TRIGGER_PERCENT`         | `60`          | Share of the window that starts a deterministic compaction.                                                      |
| `SIGIL_CONTEXT_TARGET_PERCENT`          | `40`          | Share the deterministic batch should leave behind; above it, a checkpoint is requested.                          |
| `SIGIL_CONTEXT_RECENT_REASONING_BLOCKS` | `5`           | Individual recent reasoning parts kept intact at compaction.                                                     |
| `SIGIL_CONTEXT_RECENT_TOOL_CALLS`       | `20`          | Individual recent tool calls kept intact at compaction.                                                          |
| `SIGIL_CONTEXT_SAFETY_MARGIN_PERCENT`   | `5`           | Additional window reservation for uncertainty; 0 to 99.                                                          |
| `SIGIL_CONTEXT_IMAGE_ROUNDS`            | `3`           | Rounds an image stays loaded. Older ones leave their id behind.                                                  |
| `SIGIL_CONTEXT_IMAGE_MAX_PERCENT`       | `10`          | Ceiling for the pixels that stay, as a percentage of the model window.                                           |
| `SIGIL_CONTEXT_OUTPUT_RESERVE`          | `8000`        | Tokens reserved for the response.                                                                                |

Invalid values are reported at startup instead of silently falling back. For
algorithm details and estimation boundaries, read the
[technical context reference](../../technical/internals/context.md).

## Files and discovery

These overrides select locations rather than migrating existing files:

| Variable              | Default                          | Purpose                                                               |
| --------------------- | -------------------------------- | --------------------------------------------------------------------- |
| `SIGIL_HOME`          | `~/.sigil`                       | Base directory for saved state, credentials, and global configuration |
| `SIGIL_CONFIG`        | Project and global configuration | Read a single configuration file instead of merging both              |
| `SIGIL_SKILLS_DIRS`   | Project and global skill roots   | Explicit colon-separated skill-root list                              |
| `SIGIL_THEMES_DIR`    | `<SIGIL_HOME>/themes`            | Custom terminal-theme directory                                       |
| `SIGIL_WORKTREES_DIR` | `<SIGIL_HOME>/worktrees`         | Parent directory for worktrees created by Sigil                       |
| `SIGIL_SERVICE_DIR`   | `<SIGIL_HOME>/service`           | Local endpoint and diagnostic directory                               |

See [Files and directories](../configuration/files-and-directories.md) before
relocating state. Skills have their own roots and don't automatically move with
`SIGIL_HOME`.

## Integrations and browser startup

These settings affect external tool processes or the browser surface:

| Variable                     | Default                      | Purpose                                                |
| ---------------------------- | ---------------------------- | ------------------------------------------------------ |
| `SIGIL_MCP_SHUTDOWN_TIMEOUT` | `0.2` seconds                | Grace period for a stdio MCP process to exit after EOF |
| `SIGIL_SERVER_PASSWORD`      | Generated at `serve` startup | Password for the browser surface; treat it as a secret |

For host, port, workspace, and idle-grace flags, use `sigil serve --help` or
`sigil server --help`. The API-only `server` command doesn't provide browser
login. See [Browser guide](../guides/browser.md).

## Development hooks

`SIGIL_NO_HOOKS=1` opts out of installing this clone's tracked Git hooks.
Hook-time switches, including deliberate skips, are documented in the
[Git-hook reference](../../technical/development/git-hooks.md). They aren't
application permission or sandbox settings.

---

[Documentation index](../../README.md) · [User documentation](../README.md)
