# Agents

Sigil's base prompt says _how to work_; an **agent profile** says _who is
working_. A profile is a Markdown file with a YAML frontmatter: the frontmatter
names it and declares which tools it may use, and the body is the prompt that
sets its behaviour.

## Where they are looked up

In order of precedence (the first one wins):

1. `<cwd>/.sigil/agents` — agents that belong to the project.
2. `~/.sigil/agents` (or `$SIGIL_HOME/agents`) — agents for every project.
3. The profiles Sigil ships with.

A project profile with the same name as a global one shadows it, and either of
them shadows a built-in. Profiles are flat `*.md` files; a name starting with
`_` or `.` is skipped, so a notes file next to them is not parsed as one.

Sigil ships with two profiles: `wizard`, the default general-purpose lead that
investigates and waits for plan approval before implementation, and `scout`, a
research subagent that can investigate in parallel with other Scouts. Scout has
shell and research tools; its read-only shell use is enforced by instructions,
not a command sandbox. See the
[built-in working agreements](../../technical/internals/agent-profiles.md#built-in-working-agreements)
for approval, delegation, and permission boundaries. Copy and adapt a built-in
profile when creating your own.

Create the directories yourself; Sigil reads them and does not create them. A
profile added while the session is open shows up on the next `/agent`, without a
restart.

## The file

Create a Markdown file with YAML frontmatter and the instructions the agent must
follow:

```markdown
---
name: reviewer
description: Reads a diff and reports only what would break in production.
permissions:
  all: deny # nothing, then open what a review needs
  read: allow
  web: allow
  run: # and the shell, but only for inspection
    allow: ["git diff *", "git status *", "git log *"]
    deny: ["git push *"]
---

You review code. You never edit it. Rank what you find by what it costs in
production, not by how clever the fix is.
```

| Key           | Required | What it does                                                                                                                          |
| ------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| `name`        | yes      | Identifier for `/agent` and `summon`. Kebab-case, up to 64 characters                                                                 |
| `description` | yes      | One line the picker shows so the choice is informed                                                                                   |
| `role`        | no       | `agent` (the default) or `subagent`. See [Subagents](../../technical/internals/agent-execution.md#subagents)                          |
| `max_steps`   | no       | Model calls in one run of this profile. See [Running out of steps](../../technical/internals/agent-execution.md#running-out-of-steps) |
| `channel`     | no       | Which way it may send messages. See [Talking between agents](../../technical/internals/agent-execution.md#talking-between-agents)     |
| `permissions` | no       | What the profile may use. Without it, every tool is available                                                                         |

Any other key is an error rather than a silent no-op: a typo in `permissions`
would otherwise leave the profile unrestricted, which is the opposite of what it
asked for.

## Permissions

`permissions` is a set of rules **grouped by tool**, in the order they are
written. Each key is a scope and each value is `allow`, `deny`, or a mapping
with the two.

| Scope                                                                      | What it covers                                         |
| -------------------------------------------------------------------------- | ------------------------------------------------------ |
| `all` (or `'*'`)                                                           | Every tool                                             |
| `read`, `write`, `run`, `web`, `skills`, `mcp`, `ask`, `roadmap`, `summon` | A group of tools                                       |
| `read_file`, `write_file`, …                                               | One built-in tool                                      |
| `mcp_github_*`                                                             | A glob over names — how a whole MCP server is targeted |

There are three shapes, from loosest to strictest:

```yaml
# (nothing)                     every tool, including MCP and the shell
permissions: [read, web, ask] # a bare list is an allowlist
permissions: # the explicit, ordered form
  all: deny
  read: allow
  write: allow
```

**The last matching rule wins**, so a profile reads top to bottom: close
everything first, then open what it needs. A tool rule can override its group.
For compatibility, a legacy `patch_file` restriction also applies to the unified
`write_file` tool, so migrating a profile does not broaden its access.

A scope nobody can match is an error, not a silent no-op:
`permissions: {reed: deny}` is rejected with the list of valid scopes, because a
typo would leave the tool in place while the profile reads as if it were closed.

### Granularity: paths, commands and queries

A rule can also talk about the **resource** a tool acts on. Instead of
`run: deny`, write:

```yaml
run:
  allow: ["git status *", "git diff *", "git log *"]
  deny: ["git push *"]
```

The tool stays visible and each call is judged on its own. Patterns are matched
against what the tool receives:

| Group           | Resource                 | Matching                                                                                                                                                      |
| --------------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `read`, `write` | The path                 | Matched both workspace-relative and absolute, so `*.env` blocks `.env` and `/repo/.env`. `~`, `$HOME` and `${HOME}` are expanded                              |
| `run`           | Each command             | Whitespace is collapsed, and a pattern ending in ` *` also matches the command with no arguments: `git status *` covers `git status` and `git status --short` |
| `web`           | The query or the URL     | As given                                                                                                                                                      |
| `skills`, `mcp` | The skill or server name | As given                                                                                                                                                      |
| `summon`        | The subagent name        | As given                                                                                                                                                      |

`shell` executes several commands in one call: **one** denied command refuses
the whole call, and the model reads the refusal as a failed result it can adapt
to.

An **allow list** over a resource is exhaustive: a command it does not name is
refused. A **deny list** subtracts: everything else is fine. With both, the
allow list bounds the tool and the deny list removes from it.

This is not a sandbox. It is a rule on the tool, and a shell is still a shell: a
command that is allowed can reach anything the process can reach. Prefer a
narrow allowlist over a list of patterns that try to recognize every dangerous
command.

### What a denial does

Two different things, depending on the rule:

- A rule that closes the **whole tool** removes it from the request. The model
  never sees the definition, so it cannot call it — the call fails as an unknown
  tool, not as a permission error.
- A rule narrowed by **resource** keeps the tool and refuses the call, which
  comes back as `PermissionDenied: the active agent profile does not allow …` in
  the conversation, so the model adapts inside the same turn instead of the turn
  dying.

## Switching agent

`/agent` opens the picker, `/agent <name>` switches directly, and `ctrl+a` opens
the same picker from anywhere in the prompt. The agent in effect is painted at
the left of the bar under the prompt, always — the one piece of context that
changes how every answer reads.

The choice **travels with the session**, the way the model does: `/new` inherits
the agent in effect, and `/load` brings back the one the conversation was built
under. A profile deleted from disk since then is not an error: the registry
falls back to the default and the conversation still loads.

Switching agent takes effect on the next request, without a restart. It is
refused while a turn runs: unlike a model swap, it changes the instructions and
the tool set of a run whose definitions were already computed.

The profile's body is injected as an instruction block **after** the base prompt
and before the rest, and it is rewritten rather than appended — so the previous
profile's instructions stop travelling with the conversation the moment you
switch.

---

[Documentation index](../../README.md) · [User documentation](../README.md)
