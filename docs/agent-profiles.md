# Session agent profiles

A session's effective agent profile controls its instructions and tool
permissions. The service reports that profile to connected clients; a displayed
name isn't a separate choice from the profile used to execute a turn.

## Restore a saved session

Opening a session restores its saved profile before resolving the model, tree,
and permissions. A saved profile must be selectable as a session agent; a
subagent-only profile isn't a valid root choice.

If the saved profile is missing or isn't selectable, Sigil chooses a valid
default: the selectable `wizard` profile, then the first selectable installed
profile. If no selectable profiles exist, the session has no profile. Sigil
persists the effective replacement so reopening the session doesn't keep an
invalid name. An empty saved choice follows the same default rule.

## Select a profile

In the TUI, use `/agent` or **Ctrl+A** to open the picker, or use
`/agent <name>` to choose directly. In the web client, open the current profile
in the header and choose an installed agent. The web picker shows its name,
description, and origin, but doesn't display permissions.

Selection applies to subsequent turns without restarting the service and is
saved without waiting for another turn. The service refuses changes while a
turn is running. Other connected clients refresh when the service publishes a
state change.

## Discover project and global agents

The service discovers profiles from the session workspace, not the attached
client's working directory. For duplicate names, project profiles take
precedence over global profiles, which take precedence over built-in profiles.
The roots are `.sigil/agents` in the project, `agents` under `SIGIL_HOME`
(normally `~/.sigil`), and Sigil's built-in profiles.

Switching the service workspace refreshes project profiles while retaining
global and built-in discovery. A profile available only on the server remains
selectable by a connected client.

## Read the service contract

Both `/api/state` and `/api/agent` expose agent metadata through the same
projection. Each agent row includes a `permissions` summary computed by
`ToolPermissions.describe()`. The attached TUI uses that server-provided summary
rather than reading a same-named local profile. This summary is presentation
metadata; the engine still enforces the parsed permission rules on tool calls.

The implementation lives in [composition binding](../src/engine/bootstrap.py),
[profile selection](../src/engine/agents/profile/registry.py), and
[server projections](../src/server/views.py). The
[profile scenarios](../specs/features/application/agent_profiles.feature)
record the behavioral contract.

See [the architecture](architecture.md) for service boundaries and
[storage](storage.md) for persisted sessions. Concurrent sessions and per-client
session routing are separate work; this fix doesn't implement them.
