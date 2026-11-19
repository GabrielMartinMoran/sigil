# Terminal commands

Type commands in the terminal composer. `/` filters the menu; `/help` shows the
commands available in your build. This is the terminal reference, not a promise
that every browser control uses the same slash syntax.

## Conversations and workspace

Use these commands to choose the conversation and its working state:

| Command                         | Behavior                                                          |
| ------------------------------- | ----------------------------------------------------------------- |
| `/new [--worktree <name>]`      | Start a session; `--worktree` or `-w` also creates a Git worktree |
| `/load`                         | Open a saved session, switching to its workspace if needed        |
| `/rollback [number]`            | Choose a user turn and roll back or fork from before it           |
| `/agent [name]`                 | Open the agent picker or select a named profile                   |
| `/goal [--yolo] <objective>`    | Set a session goal without starting a turn                        |
| `/goal status`                  | Show the current goal                                             |
| `/goal stop`                    | Stop goal mode                                                    |
| `/external-files [allow\|deny]` | Show or change session file-tool access outside the workspace     |

A goal is reviewed when the agent and its subagents have stopped working. If
work remains, Sigil can continue automatically. The review uses the same model
and can be wrong; it doesn't independently verify the agent's claims. The
completed goal remains visible in the sidebar.

`--yolo` lets the agent make reasonable decisions and proceed without asking for
plan approval. It disables `ask_user`, but doesn't grant additional tool or file
permissions. If a question is already open, answer or dismiss it before
activating YOLO; Sigil doesn't invent an answer or interrupt the active turn.
Sigil pauses on a blocker or when it reaches the configured
[continuation limit](environment.md). Model reviews and continuations can incur
additional costs.

Read [Rollback](../guides/rollback.md) before restoring files, and
[Safety](../guides/safety.md) before broadening access.

## Providers and context

Use these commands to control models, integrations, and request size:

| Command                             | Behavior                                                     |
| ----------------------------------- | ------------------------------------------------------------ |
| `/provider`                         | Add, rename, or remove provider accounts                     |
| `/model`                            | Pick an account's model and reasoning effort                 |
| `/default-models`                   | Set default models in project or global configuration        |
| `/effort [level]`                   | Open the effort picker or set a supported level              |
| `/mcp`                              | Inspect MCP servers and change availability for this session |
| `/context`                          | Show context usage, workspace information, and cost          |
| `/compact [--deterministic\|--llm]` | Reduce eligible model context; deterministic is the default  |

LLM compaction can make a billable provider request. See
[Context and cost](../guides/context-and-cost.md).

## Terminal display and alerts

These settings control this client's presentation rather than the model's
reasoning effort or tools:

| Command                           | Behavior                                                    |
| --------------------------------- | ----------------------------------------------------------- |
| `/reasoning-visibility [on\|off]` | Show or collapse reasoning; no argument toggles             |
| `/tools [expanded\|collapsed]`    | Show or collapse tool output; no argument toggles           |
| `/alerts [on\|off]`               | Show desktop alert status or change the preference          |
| `/theme [name\|--list]`           | Preview themes, apply one by name, or list installed themes |
| `/sidebar [on\|off]`              | Show or hide the sidebar; no argument toggles               |
| `/debug [on\|off\|--text]`        | Open or close diagnostics, or print the numeric report      |
| `/help`                           | Show commands and keyboard help                             |
| `/quit`                           | Close the terminal client                                   |

Hiding reasoning isn't the same as turning off model reasoning. Hiding the
sidebar doesn't erase the roadmap. Display choices can persist between runs.

## Commands during a running turn

Commands that change the active turn's configuration are refused while it runs:
`/agent`, `/provider`, `/model`, `/default-models`, `/effort`,
`/external-files`, `/mcp`, and `/compact`. A refused command returns to the
prompt. `/rollback` also requires the selected session to be idle. MCP changes
can be refused while subagents are working.

In the default service-backed terminal, `/new` and `/load` can change which
session this client watches without cancelling work in another session. `/quit`
detaches the client; it doesn't abort the service's work. Use the explicit stop
action when cancellation is intended.

You can set or change `/goal` while a turn is running. The active agent receives
the updated objective; the command doesn't start a separate user turn.

Read-only diagnostics and display controls can remain available. Plain messages
are [queued into the active turn](../guides/conversations.md), not sent through
a `/queue` command. To cancel work, use the
[Esc confirmation](../guides/conversations.md#stop-a-turn).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
