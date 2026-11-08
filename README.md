# Sigil

Terminal agent built with [pydantic-ai](https://ai.pydantic.dev/) and
[Textual](https://textual.textualize.io/).

## Running it

The workspace is the directory you start from, not the directory Sigil lives in:

```bash
cd ~/projects/thing
/path/to/sigil/sigil          # talks about ~/projects/thing
```

Symlink it once and the path stops mattering:

```bash
ln -s /path/to/sigil/sigil ~/.local/bin/sigil
```

The launcher resolves its own location through symlinks and never moves the
process, so `uv` runs the environment next to Sigil's code while the workspace,
the project config and `.git` are the ones where you are standing. Nothing is
written into the project you open.

Three places decide what Sigil sees:

| Where | What comes from there |
| --- | --- |
| **The directory you run it in** | The workspace the tools read and write, `.sigil/agents`, `.agents/skills`, `.sigil/config.json` (MCP), and which sessions you get (bucketed by directory) |
| **Sigil's own directory** | The base prompt, the built-in agents (`wizard`, `scout`), the theme defaults, and the `.env` it loads |
| **`~/.sigil`** (`SIGIL_HOME`) | The database (`sigil.db`), roadmaps, provider credentials, themes, worktrees |

A project profile shadows a global one, and both shadow a built-in of the same
name: opening Sigil in a project without `.sigil/agents` still gives you `wizard`
and `scout`, and dropping a `wizard.md` there takes over the name.

> **`.env`.** Sigil reads the one next to its own code, or in a parent directory of
> it — never the one in the project you opened. A project's `.env` holds that
> project's secrets; letting it reach `SIGIL_*` would make Sigil's configuration
> depend on which repository it was started in. For settings that follow you between
> projects, export them in your shell, or put them in a `.env` on the path upward
> from the Sigil directory (`~/.env`, for example, when Sigil lives under your home).

## Working with agents

Sigil's behaviour is set by an **agent profile**: a Markdown file whose
frontmatter names it and lists the tools it may use, and whose body is injected
after the base prompt. `/agent` (or `ctrl+a`) picks one, and the agent in effect
is painted under the prompt. Project profiles live in `.sigil/agents`, global
ones in `~/.sigil/agents`, and Sigil ships with one of its own.

See [Agents](#agents) for the format and the permission rules.

## Agent Skills

Sigil implements the [Agent Skills](https://github.com/anthropics/skills) protocol:
self-contained folders with a `SKILL.md` (YAML frontmatter + Markdown
instructions) and optional resources (`scripts/`, `references/`, `assets/`).

### Where they are looked up

In order of precedence:

1. `<cwd>/.agents/skills` — project skills.
2. `~/.agents/skills` — user global skills.

A project skill with the same name as a global one shadows it. You can also set
an explicit list of roots with `SIGIL_SKILLS_DIRS` (separated by `:`).

### Progressive disclosure

| Level | What gets loaded | Where it travels |
| --- | --- | --- |
| 1 | `name` + `description` | `skills` instruction block, recomputed per request |
| 2 | `SKILL.md` body | `loaded-skills` instruction block, when calling `load_skill` |
| 3 | Resources and scripts | `read_skill_resource` / `run_skill_script` result |

Level 2 goes as an **instruction block**, not as a tool result. That way it
survives compaction and does not compete for the history budget. Resources are
left out on purpose: they are read on demand, which is what keeps the block
cheap.

### Tools exposed to the model

- `list_skills(query=None)` — lists metadata of the installed skills.
- `load_skill(name)` — registers the skill and returns an acknowledgement with
  the resource inventory. The `SKILL.md` body shows up in the next request's
  instructions, under "Loaded skills".
- `read_skill_resource(name, path, max_bytes=...)` — reads a resource relative
  to the skill (UTF-8 text or base64 binary). It blocks `..` and absolute paths.
- `run_skill_script(name, script, args=None, timeout_seconds=30)` — runs a
  bundled script with the skill directory as `cwd`.

### Automatic tool logging

Tools are registered with `ToolRegistrar` instead of `agent.tool_plain` /
`agent.tool`. The wrapper logs every call (`start`, `success`, `error`, duration
and args) and the UI reflects it in the chat. It works the same for sync, async
and `RunContext` tools.

```python
from src.engine.tooling.logging import ToolLoggerHub
from src.engine.tooling.registrar import ToolRegistrar

hub = ToolLoggerHub()  # the app adds its sink on mount
registrar = ToolRegistrar(agent, hub)


@registrar.tool
def my_tool(query: str) -> str: ...


@registrar.context_tool
def my_ctx_tool(ctx: RunContext[None], query: str) -> str: ...
```

For external tools that already exist as a function:

```python
registrar.register(existing_function, name='external-tool')
```

### Concurrency between tools

pydantic-ai runs the sibling calls of the same model response as concurrent
tasks. For almost every tool that is fine. For the ones that mutate a shared
resource, two simultaneous calls stomp on each other: each one reads the
original state and the last one to write erases the work of the previous one.

A guard declares, per tool, when the call cannot coexist with the previous ones
of the same response. It receives a `ConcurrencyContext` with the effective args
and the previous calls, in emission order, and returns the reason for the
conflict or `None`.

```python
from src.engine.tooling.concurrency import ConcurrencyContext


def no_same_key(ctx: ConcurrencyContext) -> str | None:
    for sibling in ctx.siblings:
        if sibling.tool_name == ctx.tool_name and sibling.args.get('key') == ctx.args.get('key'):
            return f'{ctx.tool_name} already ran for key {ctx.args["key"]!r}'
    return None


@registrar.tool(concurrency=no_same_key)
def my_tool(key: str) -> str: ...
```

The guard is evaluated in invocation order: the first call of a conflicting group
always goes through and the following ones fail with a `ToolConflict`. The model
sees it as a failed tool result and can re-emit the call in a separate step. The
rest of the calls keep running in parallel, with no global barriers.

A direct call from code —without a `RunContext`— runs anyway, without a guard:
there are no siblings to compare against.

### What a tool is, for the permissions

A tool can declare the group it belongs to and the argument it acts on, which is
what lets a profile rule on `write` instead of listing every tool that writes,
and on the path instead of on the tool:

```python
from src.engine.tooling.permission import ResourceKind, ToolGroup,
ToolPermission

_READ = ToolPermission(groups=(ToolGroup.READ,), resource='path',
kind=ResourceKind.PATH)


@registrar.tool(permission=_READ) def read_file(path: str) -> FileContent: ...
```

The declaration travels as tool metadata: nothing in the signature changes, and
a tool that does not declare one is matched by its name alone — which is what
MCP tools and `agent.tool_plain` do.

Scripts run with the Sigil interpreter for `.py`, `bash` for `.sh`/`.bash`,
`node` for `.js`, and shebang/permissions for the rest. They inherit the
`SIGIL_SELF_GUARD` guard, which prevents launching another Sigil instance.

### `SKILL.md` format

```markdown
---
name: my-skill
description: When to use this skill and what it does.
license: MIT                 # optional
allowed-tools: read write    # optional
metadata:                    # optional
  version: "1.0"
compatibility: ...           # optional
disable-model-invocation: false  # optional
---

# My Skill

Instructions...
```

`name` must be kebab-case (max 64 characters) and `description` is the main
activation mechanism (recommended max 1024 characters).

## MCP (Model Context Protocol)

Sigil consumes [MCP](https://github.com/modelcontextprotocol) servers: the tools
they publish are added to the agent's, and their resources and prompts stay
available as meta-tools. It supports stdio and HTTP (Streamable HTTP and SSE).

### Where it is configured

Everything lives in `.sigil/config.json`, with **snake_case** keys. Two files are
read and the project one wins:

1. `<cwd>/.sigil/config.json`
2. `~/.sigil/config.json`

`SIGIL_CONFIG=/path/config.json` replaces both. The merge is per server: a
project entry with the same name replaces the global one entirely.

### Minimal example

```json
{
  "mcp_servers": {
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" }
    }
  }
}
```

That server's tools appear as `github_<tool>` (for example
`github_create_issue`). There is a complete file in
`.sigil/config.example.json`.

### Fields

| Key | Type | Applies to | Default | What it does |
| --- | --- | --- | --- | --- |
| `command` | string | stdio | — | Server executable |
| `args` | string[] | stdio | `[]` | Executable arguments |
| `env` | object | stdio | `{}` | Process environment variables |
| `cwd` | string | stdio | inherits Sigil's cwd | Process working directory |
| `keep_alive` | bool | stdio | `true` | Keeps the process alive between turns |
| `url` | string | HTTP | — | Server endpoint |
| `headers` | object | HTTP | `{}` | Headers for every request |
| `transport` | `"http"` \| `"sse"` | HTTP | inferred | Forces the transport |
| `enabled` | bool | both | `true` | Turns the server off without deleting it |
| `prefix` | string | both | server name | Tool prefix; `""` disables it |
| `include_instructions` | bool | both | `true` | Injects the server instructions into the prompt |
| `init_timeout` | number | both | `30` | Seconds to connect and perform the handshake |
| `read_timeout` | number | both | `300` | Seconds to wait for each message |
| `include_tools` | string[] | both | `[]` | Tool allowlist |
| `exclude_tools` | string[] | both | `[]` | Tool denylist |

Each server declares `command` (stdio) or `url` (HTTP), never both. Fields of one
transport on the other are an error, not ignored. `include_tools` and
`exclude_tools` are also mutually exclusive.

### Environment variables in the file

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

### `cwd`: where the server works

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
      "args": ["-y", "@playwright/mcp@latest", "--output-dir", "/tmp/sigil-shots"],
      "cwd": "/tmp"
    }
  }
}
```

### Resources and prompts

They are not tools, so they are exposed through four meta-tools:

| Tool | What it returns |
| --- | --- |
| `list_mcp_resources(server=None)` | Published resources, with URI and mime type |
| `read_mcp_resource(server, uri)` | Content: UTF-8, or base64 if binary |
| `list_mcp_prompts(server=None)` | Available prompts and their arguments |
| `get_mcp_prompt(server, name, arguments=None)` | The already rendered prompt |

Plus `list_mcp_servers(connect=False)`, which reports what is configured, what
failed and, with `connect=true`, which tools each server exposes.

With no configured servers, these tools are not registered: they do not spend
context.

### `/mcp` command

`/mcp` in the chat connects to every server and shows transport, state, tools and
the last error. Config errors also show up as a notice on startup, without
blocking the session.

### What happens if a server fails

A server that cannot connect does not break the turn: it contributes zero tools,
the rest of the conversation keeps going and the error stays visible in `/mcp`.
Parsing errors are reported per server, so a broken entry does not invalidate the
rest.

### Startup and quit

The MCP layer is built **after** the first paint, on a worker thread: building it
imports the MCP SDK, which is half a second that nothing on screen needs. Until it
is ready the agent holds an empty placeholder, so a turn that starts in that first
second simply has no MCP tools — the state of a session whose servers are still
starting. `SIGIL_MCP_SHUTDOWN_TIMEOUT` (default `0.2`, in seconds) bounds how long
a stdio server has to exit on EOF when the app quits; the ones that ignore it are
signalled, so nothing is left running.

### Limitations

Sampling, elicitation and roots are not announced to the server. They require the
client to lend the model or the UI mid-turn, and that opens recursion and
interactive questions inside a tool. A server that depends on those capabilities
will fail when asking for them.

## Agents

Sigil's base prompt says *how to work*; an **agent profile** says *who is
working*. A profile is a Markdown file with a YAML frontmatter: the frontmatter
names it and declares which tools it may use, and the body is the prompt that
sets its behaviour.

### Where they are looked up

In order of precedence (the first one wins):

1. `<cwd>/.sigil/agents` — agents that belong to the project.
2. `~/.sigil/agents` (or `$SIGIL_HOME/agents`) — agents for every project.
3. The profiles Sigil ships with.

A project profile with the same name as a global one shadows it, and either of
them shadows a built-in. Profiles are flat `*.md` files; a name starting with
`_` or `.` is skipped, so a notes file next to them is not parsed as one.

Sigil ships with two profiles: `wizard`, the general-purpose agent in effect when
nothing else is chosen or remembered, and `scout`, a read-only subagent built to
be summoned. `wizard` is the smallest complete example: copy it, rename it,
rewrite the body.

Create the directories yourself; Sigil reads them and does not create them. A
profile added while the session is open shows up on the next `/agent`, without a
restart.

### The file

```markdown
---
name: reviewer
description: Reads a diff and reports only what would break in production.
permissions:
  all: deny                    # nothing, then open what a review needs
  read: allow
  web: allow
  run:                         # and the shell, but only for inspection
    allow: ['git diff *', 'git status *', 'git log *']
    deny: ['git push *']
---
You review code. You never edit it. Rank what you find by what it costs in
production, not by how clever the fix is.
```

| Key | Required | What it does |
| --- | --- | --- |
| `name` | yes | Identifier for `/agent` and `summon`. Kebab-case, up to 64 characters |
| `description` | yes | One line the picker shows so the choice is informed |
| `role` | no | `agent` (the default) or `subagent`. See [Subagents](#subagents) |
| `max_steps` | no | Model calls in one run of this profile. See [Running out of steps](#running-out-of-steps) |
| `channel` | no | Which way it may send messages. See [Talking between agents](#talking-between-agents) |
| `permissions` | no | What the profile may use. Without it, every tool is available |

Any other key is an error rather than a silent no-op: a typo in `permissions`
would otherwise leave the profile unrestricted, which is the opposite of what it
asked for.

### Permissions

`permissions` is a set of rules **grouped by tool**, in the order they are
written. Each key is a scope and each value is `allow`, `deny`, or a mapping
with the two.

| Scope | What it covers |
| --- | --- |
| `all` (or `'*'`) | Every tool |
| `read`, `write`, `run`, `web`, `skills`, `mcp`, `ask`, `roadmap`, `summon` | A group of tools |
| `read_file`, `patch_file`, … | One built-in tool |
| `mcp_github_*` | A glob over names — how a whole MCP server is targeted |

There are three shapes, from loosest to strictest:

```yaml
# (nothing)                     every tool, including MCP and the shell
permissions: [read, web, ask]   # a bare list is an allowlist
permissions:                    # the explicit, ordered form
  all: deny
  read: allow
  write: allow
```

**The last matching rule wins**, so a profile reads top to bottom: close
everything first, then open what it needs. A tool rule beats the group it sits
in, which is how `write: allow` plus `patch_file: deny` means "create files, but
never edit one".

A scope nobody can match is an error, not a silent no-op: `permissions: {reed:
deny}` is rejected with the list of valid scopes, because a typo would leave the
tool in place while the profile reads as if it were closed.

#### Granularity: paths, commands and queries

A rule can also talk about the **resource** a tool acts on. Instead of `run:
deny`, write:

```yaml
run:
  allow: ['git status *', 'git diff *', 'git log *']
  deny: ['git push *']
```

The tool stays visible and each call is judged on its own. Patterns are matched
against what the tool receives:

| Group | Resource | Matching |
| --- | --- | --- |
| `read`, `write` | The path | Matched both workspace-relative and absolute, so `*.env` blocks `.env` and `/repo/.env`. `~`, `$HOME` and `${HOME}` are expanded |
| `run` | Each command | Whitespace is collapsed, and a pattern ending in ` *` also matches the command with no arguments: `git status *` covers `git status` and `git status --short` |
| `web` | The query or the URL | As given |
| `skills`, `mcp` | The skill or server name | As given |
| `summon` | The subagent name | As given |

`run` executes several commands in one call: **one** denied command refuses the
whole call, and the model reads the refusal as a failed result it can adapt to.

An **allow list** over a resource is exhaustive: a command it does not name is
refused. A **deny list** subtracts: everything else is fine. With both, the
allow list bounds the tool and the deny list removes from it.

This is not a sandbox. It is a rule on the tool, and a shell is still a shell: a
command that is allowed can reach anything the process can reach. Prefer a
narrow allowlist over a list of patterns that try to recognize every dangerous
command.

#### What a denial does

Two different things, depending on the rule:

- A rule that closes the **whole tool** removes it from the request. The model
  never sees the definition, so it cannot call it — the call fails as an unknown
  tool, not as a permission error.
- A rule narrowed by **resource** keeps the tool and refuses the call, which
  comes back as `PermissionDenied: the active agent profile does not allow …` in
  the conversation, so the model adapts inside the same turn instead of the turn
  dying.

### Switching agent

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

## Subagents

A session is not one agent: it is a **tree**. The agent at the root is the one you
talk to — the one `/agent` and `ctrl+a` offer — and under it hang the
**subagents** it summoned. They are not selectable, they are not listed anywhere
except the tree, and they answer to the agent that started them.

A subagent is an agent: same tools, same model, same permission rules, its own
profile and its own context. What makes it a *sub*agent is who starts it and where
its answer goes.

### The role

```yaml
---
name: scout
description: Reads one thing end to end and reports what it found.
role: subagent
---
```

`role: agent` is the default, so every profile written before this existed keeps
being selectable. A profile with `role: subagent` is refused by `/agent` — the
agent at the root of a session is the one a person talks to — and refused by
`summon` if it says `role: agent`: an agent is not something another agent starts.

### The `summon` permission

Starting a subagent is the `summon` tool, and it is ruled on like every other
tool: the **name of the subagent is the resource**.

```yaml
permissions:
  summon:
    allow: ['scout', 'review-*']   # these two profiles...
    deny: ['review-security']      # ...except this one
```

- **No `summon` rule**: every subagent is available.
- **`summon: deny`**: the tool is not advertised at all; the model does not know
  it exists.
- **An allow list**: exhaustive. A name it does not cover is refused as a failed
  call the model can read, and the block in the prompt lists only the names that
  are left — an agent that may summon two subagents is told about two.

A subagent cannot summon itself, and the chain has a cap (`max_depth`, 3 by
default): an agent may summon ten subagents in a row, but a summon inside a summon
inside a summon inside a summon is a runaway.

When a node ends, whatever it had summoned and was still running is stopped with it:
a branch cannot hang from a parent that stopped, and its answer would have nobody
to reach. That is also why a killed node takes its descendants with it.

### Sync and async

The tool takes a `mode`:

| Mode | What the caller sees |
| --- | --- |
| `sync` (default) | Nothing until the subagent finishes. Then the tool result **is** the subagent's final message |
| `async` | An acknowledgement at once. The message arrives later, as a new user message from `[subagent Scout (scout #1a2b3c, async) finished]` |

`sync` is what you want when the next step depends on the answer. `async` is what
you want when the work is independent: the parent keeps going, and the answer lands
mid-turn if the turn is still open, or wakes the session in a new turn if it is not.

**Ending the turn is how the agent waits.** The turn that dispatched the squad
finishes — the user reads "two researchers are running" — and the session goes idle.
When a report arrives, the app starts the turn it is owed, with the report in front
of it. It is debounced: three reports landing within a second are three messages and
**one** turn, because three turns would be three model requests to read three
paragraphs that belong together.

The flag that says "nobody has read this answer" travels with the session, so a
report that arrives while the app is closing is not lost: the next open finds it
waiting and starts the turn there.

An answer never interrupts a turn the user started: that turn drains the same queue
at the top of its prompt, which is why a `sync` call and a woken turn share one path.

Stopping the turn (`Esc` twice) stops the async subagents it started: a background
node still spending tokens is not stopped.

### The id, and going back for more

`summon` returns the id of the execution it started, and that id is the handle for
everything else:

| Call | What it does |
| --- | --- |
| `summon(agent="scout", task="…")` | A new node. Two calls without an id are two nodes |
| `summon(agent_id="1a2b3c", task="…")` | Continues **that** node on top of its transcript |
| `send_message(agent_id="1a2b3c", message="…")` | Steers it mid-run, or answers a notice |
| `kill_subagent(agent_id="1a2b3c")` | Stops it, and everything it summoned |
| `list_agents()` | The ids, names, states and parents of the branch |

The id is execution and the name is kind of agent: resuming `#1a2b3c` keeps the
profile it was started with, and summoning `scout` again starts from nothing.
A resume appends to the history instead of replacing it, so a subagent that read
half a tree keeps what it read.

### Running out of steps

A profile can declare its own budget:

```yaml
max_steps: 40
```

Running out is not an abort. The run loses its tools and gets **one more model
call**, with a message explaining that the budget is gone and what to write
instead: what it established, what it did not get to, and anything the caller needs
to continue. That last message is the subagent's report.

The parent can then pick it up with `summon(agent_id=…)` — the report is in the
node's transcript — instead of starting the investigation again. The tool result
says `out of steps` next to the node's id, which is the cue to do exactly that.

The turn of the session works the same way, and it is the same key: a profile's
`max_steps` is the budget of *that* agent, whether it is the one at the root or a
subagent under it. `SIGIL_MAX_STEPS` (default `256`) is the fallback for every
profile that declares nothing, which is all of them until one asks. Reaching it
ends the turn with a summary rather than a cut-off, so a long turn is still worth
reading and the next message continues from there.

### Talking between agents

A subagent can be given a voice in its frontmatter:

```yaml
channel: bidirectional          # it may send notices to its parent

channel:                        # ...and to the siblings it names
  mode: bidirectional
  parent: true
  siblings: ['reviewer', 'scout']
```

| Direction | What it is |
| --- | --- |
| Parent → child | An **instruction**: injected into the run like a message typed in the chat |
| Child → parent | A **notice**: the parent reads it and decides whether to act |
| Sibling → sibling | A **notice**, only in the direction the sender's channel names |

A notice is information, not an order, and it says so in the message the receiver
reads. Nothing travels the other way by default: no `channel` means unidirectional,
the parent talks and the subagent answers once at the end.

**A message needs a live receiver, whoever sends it.** Nothing is queued for an
agent that is not running: a stopped subagent reads nothing, and a queue nobody
promised to drain is a queue that grows in the dark. The `send_message` call fails
with an error that says what to do instead, and the advice depends on who is
asking because the fix does:

| You | Target | What the refusal tells you |
| --- | --- | --- |
| Parent | Your child, finished | `summon(agent_id="…", task=<your message>)` — it keeps its transcript |
| Child | Your parent, not in a turn | You cannot start it: put it in your final report |
| Sibling | A sibling, finished | A sibling cannot resume it: ask your parent |

So answering a notice is a `summon` with the reply as the task, which is exactly
what the parent's own error says. One gesture, and the subagent reads the answer on
top of everything it already knew.

**A child never outlives its parent.** When a node ends, whatever was still running
under it is stopped, and a killed node takes its descendants with it. There is no
state where a branch hangs from an agent that is gone.

The ids a child needs to write to its neighbours are not something it is asked to
remember: a block of the prompt — **The agents around you** — lists its parent, its
children and the siblings its channel names, with the id and whether each one is
working right now. It is rebuilt on every request, so a sibling that finished stops
being reachable the moment it does.

### The tree, on screen

| Key | What it does |
| --- | --- |
| `ctrl+t` | The agents of the session: every node, its state, how long it has been running |
| `Enter` | Open the highlighted node |
| `b` | Send it to the background: stop waiting, keep the work going |
| `k` | Stop it, and everything it summoned |
| `Esc` (inside a subagent) | Back to its parent |
| **Click a spawn in the chat** | Open that subagent |

`b` is the intuitive version of the tool's `mode="async"` on a call that is already
blocking the turn: the caller stops waiting, the node keeps working, and its answer
arrives as a message. `k` is what makes a background subagent safe to start: without
it, the only way to stop one would be to quit.

Choosing a row opens that subagent in place of the chat: its transcript — the same
blocks, gutters and collapsed reasoning as the main conversation — under a header
with where it hangs, how it ended and how long it took. The task is not repeated up
there: it is the first message of the transcript, and the header stays fixed while
that transcript scrolls. The composer is locked there, because a subagent answers its
parent and a message typed into its transcript would be read by nobody. `Enter` on
the root row takes you back to the conversation.

The spawn block in the chat is the other way in: once it knows which execution it
started, its name is underlined, the block highlights on hover, and a click opens the
node. It works on a session loaded from disk too, where the id comes back inside the
answer rather than in the call.

The tree is **persisted with the session**, like the history: `/load` brings back
the subagents that conversation summoned, with their transcripts and how each one
ended. A resumed session that forgot what ran under it would be a worse record
than the chat itself.

### What belongs to one agent

Three things used to be session-wide and are now per node, because a tree makes the
difference visible:

| State | Why it is per agent |
| --- | --- |
| **Loaded skills** | A loaded skill injects its whole body into the instructions. Sharing them would put the Lead's design guidance into an engineer's prompt and spend its budget on it |
| **The roadmap** | A plan belongs to whoever executes it: "what is the squad doing" is not "which unit am I in" |
| **Context and cost** | The panel measures the transcript of whoever is on screen |

The side panel follows the screen: opening a subagent shows its name at the top of
the column — the rest of the panel looks the same for a parent and its child — and
its plan, its skills, its context and its bill. The terminal window title carries the
node too, so a reader whose terminal is not focused still knows which conversation is
open.

## Built-in tools

Besides the skills and the MCP servers, Sigil registers a set of its own tools.
They all go through the same logger, so every call shows up in the chat with its
duration and its arguments.

| Tool | Group | What it does |
| --- | --- | --- |
| `run` | `run` | Runs a shell command with a timeout and an anti-self-launch guard |
| `read_file` | `read` | Reads a file, or a line range, with a byte limit |
| `write_file` | `write` | Creates a file, or replaces or appends its content |
| `patch_file` | `write` | Edits a file by exact text or by line range |
| `web_search` | `web` | Searches the web and returns each result's title, URL and snippet |
| `read_url` | `web` | Extracts a page as Markdown, text or HTML |
| `list_skills`, `load_skill`, `read_skill_resource`, `run_skill_script` | `skills` | Discovers, loads and reads Agent Skills; a bundled script also counts as `run` |
| `list_mcp_servers`, `list_mcp_resources`, `read_mcp_resource`, `list_mcp_prompts`, `get_mcp_prompt` | `mcp` | The meta-tools over MCP servers, resources and prompts |
| `ask_user` | `ask` | Asks the user with options, inside the UI |
| `update_roadmap` | `roadmap` | Publishes and updates the roadmap of the current task |
| `summon` | `summon` | Starts a subagent, or resumes one by id. See [Subagents](#subagents) |
| `send_message` | `summon` | An instruction to a child, or a notice to a parent or a sibling |
| `kill_subagent` | `summon` | Stops a subagent and everything it summoned |
| `list_agents` | `summon` | The ids, names, states and parents of the agents of the session |

The group is what a profile's `permissions` rules on, together with the tool
name: `permissions: {write: deny}` closes `write_file` and `patch_file` in one
line. A tool declares its group once, next to its definition, and
`tests/test_tool_groups.py` fails if the declaration and the documented table
drift apart.

A tool error does not cut the turn: it goes back to the model as a failed result
(`outcome: failed`) and the agent decides how to proceed. If a `patch_file`
`old_string` does not appear, the model reads the error and corrects the call
instead of handing control back to you mid-task.

The one deliberate exception is a cancelled `ask_user`: that is not a tool
failure but your decision to stop. It ends the turn and returns control to you,
with the partial history kept and the unanswered call closed out.

### Files

`read_file`, `write_file` and `patch_file` work inside the workspace and inside
the OS temporary directory: any other path is rejected with an error, it is not
resolved silently.

The temporary directory is allowed because scratch files are what it is for. A
command writes one, the model reads it back, and refusing the path would only
push the work into the shell. The directory is resolved on every call, so
`TMPDIR` is honoured; on POSIX, `/tmp` and `/var/tmp` are accepted as well,
because a redirect to `/tmp` does not read `TMPDIR` either.

The allowance is the temporary tree itself: `..` does not climb out of it, and a
symlink is followed before the check, so it cannot be used to reach a file
outside. A profile still rules on these paths like any other, so `write: {deny:
['/tmp/*']}` closes the scratch area for an agent that must not have one.

`patch_file` has two modes:

- **Exact text** (`old_string` + `new_string`): the recommended mode. The text
  must appear exactly once, unless `replace_all=true`. If it appears several
  times, the error lists the lines where it is.
- **Line range** (`start_line` + `end_line`): useful when you already know the
  line numbers. You can validate the current content with `expected_text`, the
  text you expect to find in that range; the trailing newline is ignored.

Both modes respect the file's line endings: a CRLF file stays CRLF after the
patch.

`write_file` and `patch_file` declare a concurrency guard: two calls that write
the same file in the same response clash. The first goes through and the second
fails with a message asking the model to re-emit it in a separate step. Editing
different files keeps running in parallel, and reading a file never clashes with
editing it.

### Web

`web_search` uses [ddgs](https://pypi.org/project/ddgs/) and needs no API key; it
returns up to 20 results per query. `read_url` converts the page to Markdown by
default (`text_markdown`) and clips the result at 40,000 characters. Binaries
come back in base64.

### Interactive questions

`ask_user` asks 2 or 3 options per question —the recommended one appears
marked— plus a free field to type another answer. The agent uses it when the
requirements are ambiguous or an action needs explicit authorization.

The questions appear **in place of the composer**, across the whole width, right
above the status bar: not in a modal, and not in a box inside the conversation.
The box that owns the keyboard is the box on screen — a visible composer that
cannot be typed into is a lie, and it was the most prominent thing on the screen.

While a question is open the conversation stays readable above it, and **both
columns give up height, not their place**: the panel is a sibling of the row that
holds the transcript and the side panel, so it takes its room from that whole row.
The side panel keeps its width and scrolls, so a shorter one is still usable — and
its roadmap is often exactly what the question is about. A half-typed message
survives too: the composer is hidden, not emptied, and it returns focused with your
text.

While a question is open **the panel owns the keyboard**. Clicking the
conversation or the panel's own frame to read something does not take the keys
away: the arrows keep moving the highlight and `Enter` keeps answering.
Answering is the only thing the turn is waiting on, so it is what the keyboard is
for; the composer takes the keys back the moment the question closes.

Each question keeps its own answer. Moving between them never loses anything:

| Key | What it does |
| --- | --- |
| `↑` / `↓` | Moves the highlight in the list |
| `Enter` | Answers with the highlighted option, or with the typed text |
| `alt+←` / `alt+→` | Goes back and forward between questions |
| `Tab` | Moves between the list and the free field |
| `ctrl+↑` / `ctrl+↓` | Scrolls the conversation without leaving the panel |
| `Esc` | Stops the turn: the panel closes and the prompt comes back |

Going back shows what you chose: the option you picked appears with a `✓`, the
text you typed is still in the field, and the panel keeps a short summary of
what was already answered. The questions close once all of them have an answer,
and if you try to finish early, the panel takes you to the one that is still
missing.

Each option is one line, clipped with `…` when the terminal is narrow, and the
clipped part is always the description: the label and the `[recommended]` tag are
never the part that goes. If the panel runs out of height it scrolls, but the key
legend underneath stays pinned and visible — a question you cannot finish reading
is a question you cannot answer.

`Esc` is the way out of a question you do not want to answer, so it ends the
turn instead of picking something for you: the agent stops where it is, the chat
shows `Turn cancelled.` and control goes back to you. Nothing is lost: the
messages already exchanged stay in the session, the unanswered call is closed
out, and the next message starts from a valid history.

Here `Esc` is a single press, not the two-press confirmation that stops a plain
turn: the panel is already asking you something, and adding a confirmation would
mean answering one question in order to decline another.

That single press works from anywhere while the question is open. If the key
does not reach the panel — nothing is focused, or a widget in between took it —
the app asks the panel first, so `Esc` cancels the question instead of arming the
abort. The two are very different outcomes: cancelling closes the question and
stops the turn cleanly, while the abort leaves the question on screen with a run
that is no longer there to read the answer.

Cancelling is not an error and not an answer. The run stops, so the model never
gets to react to the cancellation and never decides what to do next on its own.

### Roadmap

`update_roadmap` maintains the task list at the end of the sidebar. Each item
stays in `pending`, `in_progress`, `done` or `blocked`.

The panel shows a single list in plan order, without grouping by status. The icon
and the color are enough to read it at a glance:

| Status | Icon | Style |
| --- | --- | --- |
| `done` | `✓` | green, strike |
| `in_progress` | `▶` | blue |
| `pending` | `○` | white |
| `blocked` | `!` | yellow |

The roadmap belongs to the agent that executes it, not to the directory: the root has
the plan of the session and a subagent plans for its own task. The state is rows in
`~/.sigil/sigil.db`, keyed by execution. If the write fails, the list keeps working in
memory and the error stays in `persistence_error`.

The list scrolls with the panel, so a long plan never grows the sidebar past the
screen. It does not scroll to follow the agent: the plan is read as a whole, and
a list that jumped every time an item changed state would be unreadable at
exactly the moment it is worth reading.

## Workspaces and worktrees

A **workspace** is the directory Sigil works in. When that directory belongs to a
git repository it is one of its *worktrees*: the main one, or a linked worktree
created with `git worktree add`.

```
~/.sigil/worktrees/<repo>/<name>     ← created by /new --worktree
```

`/new --worktree <name>` creates that worktree on a new branch `sigil/<name>`
from `HEAD`, moves the process there and starts the session in it:

```
/new --worktree fix-login     # also -w, --worktree=fix-login and -w=fix-login
```

The worktree directory lives **outside** the repository (under
`SIGIL_WORKTREES_DIR`, `<SIGIL_HOME>/worktrees` by default) so that the file tools
of one workspace cannot reach another's tree: `read_file` and `write_file` are
limited to the active workspace, plus the OS temporary directory, which no
workspace owns.

What a new worktree does **not** get, because it is not in the commit:

- uncommitted changes of the current workspace,
- ignored files: `.env`, `.venv`, `.sigil/config.json`.

Sigil says so in the chat when the worktree is created. The MCP configuration is
read **once**, at startup, from the directory Sigil was launched in: a worktree
usually has no `.sigil/config.json` of its own, and reloading it per workspace
would leave the session without MCP servers.

### Sessions and workspaces

A session belongs to the workspace it was created in, and every workspace's
sessions live in the same database (`$SIGIL_HOME/sigil.db`). `/load` lists the
sessions of **every** workspace of the repository, labelled:

| Label | Workspace |
| --- | --- |
| `main` | the repository's main worktree |
| `<name>` | a linked worktree (`fix-login`) |

```
● main          1a2b3c4d  fix the login form
  fix-login     9f8e7d6c  try the new parser
```

`●` marks the workspace in use, and the last column is the session title. Choosing
a session of another worktree switches to
it (the same rebind as `/new --worktree`) and then loads the session: a session is
never opened against a tree it does not belong to.

Starting Sigil in a subdirectory of a worktree is fine: the subdirectory is its own
workspace for sessions, and it shows the label of the worktree that contains it.
The side panel no longer repeats the workspace — the status bar carries it — and
`/context` prints its root and branch.

A session whose worktree was `git worktree remove`d stops being listed: there is no
workspace left to open it against. The file stays on disk.

A session is its id: the store reads `<id>.json` and lists nothing else. A file with
any other name — a copy left by the file manager (`<id> (Copy).json`), a restored
backup — carries the id of a session that is already listed, so it's skipped instead
of showing up twice.

## Working while the agent runs

The agent does not take the keyboard hostage. While a turn runs you can keep
typing: a plain message is **queued** for that turn and injected into it, and
only the commands that would pull the session out from under the running agent
are refused.

The keyboard has exactly one owner, and reading the interface does not cost it:
clicking the transcript, the side panel or the debug view scrolls and selects
but leaves the keys in the composer. The conversation is read from the composer
with the keyboard:

| Key | What it does |
| --- | --- |
| Mouse wheel | Scrolls whatever is under the pointer |
| `pgup` / `pgdn` | Moves the conversation one page |
| `ctrl+↑` / `ctrl+↓` | Moves it a few lines |

The same `ctrl+↑` / `ctrl+↓` pair reads the conversation while a question panel
is open, so the keys do not change meaning between the two surfaces.

### Sending a message mid-turn

Type it and press `Enter`, exactly as when the agent is idle. The chat shows it
with a `queued for the running turn` mark until the run reads it.

Delivery happens at the earliest point where the conversation is still valid:

- the agent finishes the tool batch it is waiting on, and **the message lands
  right after the tool results**, before the model decides what to do next;
- or the agent was about to end the turn, and the message gives it one more step
  instead of becoming a separate turn.

Either way the model reads it as a new user instruction, so a correction arrives
while the work is in progress. The chat, while the tool is still running and
right after the message is read:

```text
› refactor the parser                                    │  › refactor the parser
› use the new tokenizer  · queued for the running turn    │  › use the new tokenizer
                                                         │  ↳ 1  """Parser entry point."""
```

On the left the message is on screen immediately, marked as waiting; on the right
the mark is gone because the run has already injected it. The model reads it after
the tool result it was waiting for, before deciding what to do next.

The chat is a live log, so what it shows is the order things arrived: a message
typed before a tool finished appears above that tool's result. What the model
reads is the order in the history, which is the one that matters and the one
`/load` renders back.

The queue is not a second conversation: the message goes into the same history,
in order, and the session on disk records it in the position the model saw it.

A message that misses the last seam -- the turn ended before it could be
delivered -- goes back to the prompt, and the chat says so. It is never dropped
silently.

### What you can still do mid-turn

| Command | Mid-turn |
| --- | --- |
| `/context`, `/debug`, `/sidebar`, `/mcp`, `/help` | Available: they only read state |
| `/new`, `/load`, `/provider`, `/model`, `/effort`, `/quit` | Refused: they would swap the session, the model or the process under the running turn |

A refused command does nothing beyond saying so: the chat gets one yellow line
naming it, and the command goes back into the prompt — the turn, the session,
the history and the chat are left exactly as they were. It is refused whether
you type it or pick it from the `/` menu with `Enter`.

`/model` and `/effort` are refused up front, before the catalogue is fetched and
the picker opens. The service would also refuse the change, but only after a
network round-trip and a modal you would have to cancel.

## Stopping a turn

`Esc` stops the agent, and it asks first: stopping throws away what the model was
writing, so it is not a single keystroke's decision.

1. Press `Esc`. The status bar under the prompt says
   `Press Esc again to stop the agent.`
2. Press `Esc` again within three seconds. The turn stops.

Anything else cancels the request: any other key, or the three seconds running
out. The bar goes back to empty and the agent keeps working.

Stopping is not a failure and not an error:

- The run is cancelled where it is, and the shell commands it spawned are killed
  by process group, so a build or a `sleep` does not outlive the turn.
- The chat says `Turn cancelled.`
- What the run completed stays in the session. The tool calls that were cut off
  mid-flight are closed out, so the next message starts from a history a
  provider accepts.

`Esc` inside an `ask_user` panel is different: there it closes the panel and ends
the turn in one press, because the panel is already asking you something and a
confirmation on top would be a second question to answer.

## Turn limits

A turn ends early for two reasons.

`SIGIL_MAX_STEPS` (default `256`) is the budget. One *model call* counts as one
step. When it runs out the turn is not aborted: the tools are taken away and the
model gets one last call, with a message explaining that the budget is gone, to
write what it established and what is left. The chat says so, and what was
completed stays in the session — so a long turn ends with a report instead of
stopping mid-sentence.

The other reason is you, with `Esc` twice (see
[Stopping a turn](#stopping-a-turn)) or with a single `Esc` inside an `ask_user`
panel. The turn stops on purpose, so no error is reported; the chat says
`Turn cancelled.`, and the history keeps what already happened.

Either way the session stays usable and the next message starts a new turn
instead of failing on a call that never got its result. Sigil closes the gap
itself when the turn ends, so a call cut off mid-flight does not survive in the
saved history.

pydantic-ai brings its own cap of 50 requests per run. Sigil disables it
(`request_limit=None`) so the only limit is the step one: otherwise a long turn
died with a `UsageLimitExceeded` nobody configured, before reaching the real
limit.

### A crashed turn does not break the session

A turn is saved after every step, which is what lets a long turn survive the
process dying halfway: what the run completed is already on disk. The flip side
is that the last save can land in the middle of a tool batch, with a model
response that asked for tools and no results behind it.

pydantic-ai refuses a new prompt on top of that, and it refuses it every time:
one crash that lands in that window used to break the session for every message
after it, with `Cannot provide a new user prompt when the message history
contains unprocessed tool calls` in the chat.

Sigil checks the history at both ends of a turn:

1. At the start, before anything reaches the agent. This is the case a crashed
   process leaves behind: the app is opened again on a broken session.
2. At the end, on every path. A cancelled turn, a turn that died on an error
   while a tool ran, and a step limit all leave a tail a provider would reject,
   and the save that follows would write it to disk.

Either way the damage is closed out. When the repair happens at the start of a
turn the chat says `Recovered the session: …`, because that is damage a previous
process left behind, and you need to know the session you opened was not what
you saved. When it happens at the end of a turn the chat has already reported
what ended it (`Turn cancelled.`, the error, the step limit), so the repair stays
quiet.

The synthesized results are marked with `pydantic_ai_synthesized_tool_return`,
so nothing pretends a tool ran when it didn't. A healthy history is left exactly
as it was — no copy, no reordering — so the model's prompt cache prefix is never
churned.

## Providers and models

Sigil talks to three provider families, and everything about them is configured
from the UI: no key goes in an environment variable and nothing needs a restart.

| Provider | Product | Sign-in | Models come from |
| --- | --- | --- | --- |
| OpenRouter | — | API key | its public catalogue |
| OpenCode | Zen | API key | its public catalogue |
| OpenCode | Go | API key | the Zen catalogue plus free text (Go publishes no list) |
| OpenAI | — | API key or ChatGPT plan | `/v1/models` plus pydantic-ai's profiles |

### Accounts

An **account** is one credential plus a name you choose. You can have as many as
you want, including several of the same provider — two OpenRouter keys, a personal
and a work OpenAI org. Manage them with `/provider` and choose a model across
accounts with `/model`.

```
/provider                 manage accounts: add, rename, remove
/model                    choose a model across accounts, then its reasoning effort
/effort [level]           open the effort picker, or set a level directly
```

| Shortcut | What it opens |
| --- | --- |
| `ctrl+a` | the agent picker |
| `ctrl+l` | the reasoning-effort picker |
| `ctrl+m` | the model picker |
| `ctrl+p` | the searchable command palette |
| `ctrl+v` | paste an image from the clipboard (or text) |
| `alt+d` | the agent debug view |
| `ctrl+t` | the agents of the session, and the way into any subagent |

`ctrl+m`, `ctrl+l` and `ctrl+a` are read by the prompt itself rather than bound
globally, so Enter keeps working: on a terminal without the Kitty keyboard
protocol, Ctrl+M and Enter are the same byte (CR, `0x0D`). Textual enables the
protocol's "disambiguate escape codes" flag, so a capable terminal sends a
distinct sequence for Ctrl+M and all the shortcuts work; a legacy terminal sends
Enter and the message is sent, exactly as before. Nothing is swallowed either
way. `ctrl+a` is `home` in a text input, and that is precisely why it is read
here: the prompt claims it before the widget turns it into a cursor move.

In the model picker, type to filter by account or model, and press `ctrl+f` to
mark or unmark a favorite. Favorites appear first and remain saved between runs.
Selecting a model opens the effort picker; `Esc` cancels without changing the
active selection. If no model matches, you can use the typed ID for an account.

In the command palette, type to filter, use `↑`/`↓` to move, and press `Enter`
to run the selected command. The worktree entry requests a name before running
`/new --worktree`. `Esc` cancels without changing your draft.

In the model picker, `↑`/`↓` move, `PgUp`/`PgDn` jump, `Enter` chooses, `Tab`
completes the highlighted model, and `Esc` goes back. In the command menu, `Tab` completes the highlighted command and adds
a trailing space when it takes an argument.

Adding an account asks for the provider, the product and the sign-in method, and
only offers the combinations that exist. The key is checked before it is saved:
a key the provider rejects is never written. If the check cannot decide (you are
offline, or the plan's quota is spent), the account is saved anyway with a
warning — a bad network must not be a dead end.

Switching an account, a model or the effort takes effect on the **next turn**,
with no restart: the agent holds a router whose target is swapped between turns.

### What a model choice carries

Each catalogue entry brings the context window and the reasoning levels that
model actually accepts, so:

- the side panel shows the real window instead of a guess;
- `/effort` only offers levels the model supports, and refuses the others with
  the list of what it does accept;
- switching models keeps the effort when the new model accepts it, and falls back
  to the model default when it does not.

Both values are stored with the selection, so a cold start knows them without a
request; the app also refreshes the catalogue once on startup when it has nothing
cached, which is what fills the effort picker for a model chosen in a previous
session.

If the catalogue cannot be reached, the built-in list is used — and it carries the
window and the levels too, because those are persisted: a fallback that claimed
"no effort control" would write that into the state file and the picker would stay
empty even after the network came back.

### Where it is stored

```
~/.sigil/
  sigil.db                  0644  accounts, preferences, sessions, plans
  providers/
    credentials.json        0600  the keys and OAuth tokens
```

The accounts — alias, provider family, product, sign-in method — and the
preferences — last model, OAuth client registrations — are rows in the database,
like everything else Sigil writes while you work. The credential is the one thing
that stays a file, because secrets do not share permissions with data that must be
world-readable.

`credentials.json` is created owner-read-only. It is a plain file, not an OS
keyring: anyone who can read your home directory as your user can read it, which
is the same exposure as a `.env` but with the right permissions and no risk of
committing it. Deleting an account deletes its credential.

Set `SIGIL_HOME` to move all of it somewhere else (a test sandbox, an encrypted
volume).

### A session remembers its model

The model, the account and the reasoning level are saved with the conversation,
together with the context window and the accepted effort levels. `/load` restores
them, on purpose: resuming a long conversation on a different model would silently
change cost and behaviour. If that account was removed, the current model stays and
the chat says why.

Carrying the window is what makes a cold start show the real context size instead
of `?`: the catalogue is a network resource, and the side panel must not depend on
a request to paint.

### A session is named after its first message

The first message of a session is sent once to the model the conversation already
runs on, with a one-line instruction to name it, and the answer becomes the session
title. It costs one cheap request (reasoning off) and nothing to configure: the
account you picked answers it, and the answer the user is waiting for never waits
on it, because the request runs on a worker while the turn does.

The title is shown in two places:

- **The terminal window title**, as `<repository> — <session title>`, so a row of
  open terminals says which project and which conversation each one is. A linked
  worktree adds its own label (`sigil:fix-login`), because two worktrees of the
  same repository are two different trees. Outside a repository the folder name
  takes the place of the repository.
- **`/load`**, where each row shows the title instead of the raw first message.

The title is a label and never a dependency. If the request fails, answers nothing
usable or the terminal ignores the escape sequence, the session keeps working and
the listing falls back to the first message cut to 60 characters. A session keeps
the first title it gets: renaming it on every message would make the window change
under the user while the conversation drifted away from the label.

<!-- prettier-ignore -->
> [!NOTE]
> The window title is an escape sequence (`OSC 2`), supported by xterm, VTE
> terminals, kitty, iTerm2 and Windows Terminal. A terminal that ignores it simply
> keeps its own title; nothing else changes.

## Commands

Commands are typed in the prompt and start with `/`. The menu filters them as you
type.

| Command | What it does |
| --- | --- |
| `/new [--worktree <name>]` | Starts a new session, with its empty roadmap; `--worktree` creates a git worktree and works there |
| `/load` | Loads a saved session and its roadmap, switching workspace if it belongs to another worktree |
| `/agent [name]` | Opens the agent picker; with a name, switches directly |
| `/provider` | Manage provider accounts: add, rename, remove |
| `/model` | Pick a model across accounts, then its reasoning effort |
| `/effort [level]` | Opens the effort picker; with a level, sets it directly |
| `/reasoning-visibility [on\|off]` | Shows or collapses the reasoning in the chat; without an argument, toggles |
| `/tools [expanded\|collapsed]` | Shows or collapses the tool output in the chat; without an argument, toggles |
| `/alerts [on\|off]` | Shows desktop alert status; enables or disables alerts |
| `/theme [name\|--list]` | Opens the theme picker with a live preview; with a name, applies it; `--list` lists the installed ones |
| `/sidebar [on\|off]` | Shows or hides the sidebar; without an argument, toggles |
| `/mcp` | Queries the state of the MCP servers |
| `/context` | Shows the breakdown of the used context and what the session cost |
| `/debug [on\|off\|--text]` | Shows or hides the agent debug view; without an argument, toggles |
| `/help` | Lists the available commands and the keys |
| `/quit` | Exits Sigil |

While a turn runs, only the read-only commands are available: `/context`,
`/debug`, `/sidebar`, `/mcp` and `/help`. `/new`, `/load`, `/agent`, `/provider`,
`/model`, `/effort` and `/quit` are refused: they would swap the session, the
agent, the model or the process under the running turn. A refused command comes
back into the prompt after one yellow line in the chat, and nothing else
changes.

The sidebar starts visible. Hiding it does not erase the roadmap: the agent keeps
updating it and it comes back with `/sidebar on`.

### The composer

`Enter` sends the message and `Shift+Enter` adds a line. The composer grows with
the message up to ten rows and then scrolls, so a pasted stack trace does not
push the conversation off the screen.

`Shift+Enter` needs a terminal that reports modifiers (the Kitty keyboard
protocol, which Textual enables on capable terminals). On one that does not, the
terminal sends a bare carriage return and the key reads as `Enter`, so the
message is sent instead of split. That is the same behaviour every other
line-oriented chat has, and it fails towards the familiar thing rather than
towards a lost keystroke.

### Pasting an image

`ctrl+v` with an image on the clipboard attaches it to the message. A terminal
carries text and nothing else, so the bytes are read from the desktop, and the
composer writes `[Image #1]` where the cursor was:

```text
› explain what breaks in this diff [Image #1] and compare it with the test
```

The marker is the message: the model reads that text and the picture beside it,
in the same order, so "the second screenshot" means what it says. It is painted
in reverse video to stand out from what you typed, and it is deleted as **one
block**: a single `backspace` removes the whole marker, the image goes with it,
and the remaining markers are renumbered, so `[Image #2]` never survives the
deletion of `[Image #1]`.

Each image is bounded before it is sent: the longest side is scaled to 1568 px,
a PNG stays a PNG (recompressing a terminal screenshot as JPEG fuzzes the text),
a JPEG stays a JPEG, and anything above 4 MB is compressed further. The chat shows
what travelled: `▣ 1 image (1280×720 PNG)`.

**Clipboard tools.** The bytes come from the desktop, and each one needs its own:

| Desktop | Needs | Install |
| --- | --- | --- |
| X11 | `xclip` | `apt install xclip` |
| Wayland | `wl-paste` | `apt install wl-clipboard` |
| macOS | `pngpaste` | `brew install pngpaste` |
| Windows | PowerShell (built in) | — |

If the tool is missing, the composer says so once and pastes text as usual: the
feature degrades to the behaviour that existed before, it does not break pasting.
Wayland is detected from `XDG_SESSION_TYPE` (or `WAYLAND_DISPLAY` when the session
type is not declared) and never falls back to `xclip`, which in an XWayland session
answers from a stale X selection.

**Only multimodal models.** A model whose catalogue declares text only refuses the
paste with a line naming `/model`; the turn is not spent on a 400. A catalogue that
says nothing is assumed capable, because refusing a paste the model would have
accepted is the worse mistake. The flag travels with the saved selection, so a
cold start decides without reaching the network.

An image pasted mid-turn (see [Sending a message mid-turn](#sending-a-message-mid-turn))
goes into the running turn like any other message.

**What it costs.** The context meter prices an image by its pixels — patches of
28×28 — not by the size of the file, and images from the turns the view has moved
past are dropped from the request with a note where they were, because a
screenshot costs full price forever while being useful for about one turn. They
stay in the session on disk: the note says so, and asking about one again
re-attaches it.

### Status bar

The line under the composer answers four questions, left to right.

**Who?** The **agent in effect**, always visible. It is the one piece of context
that changes how every answer reads, and having to open a modal to remember which
agent is talking is exactly the confusion the bar removes.

**Is it working?** A spinner and the verb of the moment — `Thinking`, `Writing`,
`Running read_file` — with the elapsed time once a turn passes a second. Between
turns it rests on `● Ready`. Without it, an app waiting on the model looks
exactly like an app waiting on you.

**Where?** The worktree and the branch the agent is working on: `main main` for
the main worktree, `fix-login sigil/fix-login` for a linked one. With worktrees in
the picture, two sessions look the same and write to different trees.

**How much?** `+18 −4  1 new`: the added and removed lines of the working tree in
the green and red `git diff` uses, plus a count of the files git does not track
yet. It comes from `git diff --numstat HEAD`, so staged and unstaged changes are
counted together. Untracked files are a count and not a line count, because
`git diff` has no diff for a file outside the index and any number there would be
an invention.

```
agent Wizard  ·  ⠋ Thinking 12s           main sigil/refactor-ui  +18 −4  1 new
```

A transient message (`Press Esc again to stop the agent`) takes the place of the
activity group for as long as it is up, so the bar does not grow a second line for
two words. It never takes the agent: what answers is not transient.

The summary is read off the event loop after every tool call and on a 1.5-second
poll; a tool can change the tree through a shell command the file tools never see,
so there is no event to subscribe to. Outside a git repository the section does
not appear at all.

### Sidebar

The sidebar is one panel with the sections the session needs, in the order they
are read. The diagnostics —what the agent is working with— come first, and the
roadmap of the task closes the list: they are read together, so a second box
under the first only cost a border, a margin and the height of a column that sat
mostly empty.

The sections, from top to bottom:

- **Model**: the active account alias, its provider family, the model, the
  reasoning level in effect, the context budget of that model's window, and the
  cost of the session so far.
  Before anything is configured it says `not configured — /provider`, which is
  the first thing to do on a fresh install.
- **Loaded skills**: the skills the agent loaded in this session, in load order.
  It is the quick diagnostic of whether it is following the protocol.
- **MCP**: the count goes inline with the title, in parentheses. Below, each
  server is listed with the same `•` the skills above it use: green for the ones
  that work, `✗` in red for the ones that do not. With no configured servers, the
  section does not appear.
- **Roadmap**: the plan of the task, one line per item, with its notes. It is the
  last section, and the one read after every message.

The context budget is the last line of the model section and not a section of
its own: it is a property of that window, it is read with the model, and a
heading for one number cost two rows of a 34-column panel. It reads like the
line above it, and the bill of the session closes the same block:

```
Model
 work · OpenRouter
 anthropic/claude-sonnet-4
 effort: high
 context: 66% (132K / 200K)
 cost: $0.04

Loaded skills
 • clean-code
 • frontend-design

MCP (3, 1 ✗)
 • github
 • playwright
 • filesystem
 ✗ broken

Roadmap
 ▶ Rewrite the tool renderer
 ✓ Read the current one
 ○ Update the tests
```

The percentage colours green, yellow and red across 70% and 90%, and the pair it
comes from rides in parentheses. The tokens are compacted so it fits in the
column: `K` for thousands and `M` for millions, no decimals once the number is
`>= 100K` and never for millions. Below 100K a single decimal survives when it
says something —`12.4K`, `5.5K`— and disappears when it would read `.0`.

The whole panel scrolls, not just the roadmap. With one box there is one
scrollbar: nesting a scrolling list inside a scrolling panel would put a
scrollbar inside a scrollbar in a column that is 34 characters wide. A plan of
thirty items and twenty loaded skills stay inside the panel instead of pushing
the composer off the screen.

`/sidebar off` hides the panel, and `/sidebar on` brings it back. Hiding does
not erase anything: the agent keeps updating the roadmap while it is out of
view.

The MCP count refreshes on startup (in a worker, without blocking the UI) and
every time you run `/mcp`.

#### Session cost

The `cost:` line is what the session has spent so far. It comes from the
provider whenever the provider reports it: OpenRouter sends the amount it is
going to charge for a request, and that number wins, because it is the invoice
and not an estimate. Every other request is priced by `genai-prices`, the
list-price table pydantic-ai ships. The panel marks that case with `~`, since a
list price is not a charge.

A model no table knows is left out rather than counted as zero, and a session
with such a request shows `+`: the amount is a floor.

| Line | Meaning |
| --- | --- |
| `cost: $1.20` | Every request was priced by the provider: this is the bill. |
| `cost: ~$0.04` | Priced at list price. A reseller, a discount or a plan can charge something else. |
| `cost: $0.04+` | At least this much. Something in the session could not be priced. |
| `cost: ~$0.04+` | Both: a list price, and incomplete. |
| `cost: ?` | Nothing in the session could be priced at all. |
| `cost: $0` | Nothing has been sent yet. |

Amounts under a cent keep four decimals, because that is what the first requests
of a session cost and `$0.00` would read as free. Above a cent they are rounded
to cents.

A subscription is the case no estimator can answer: signing in with a ChatGPT
plan bills nothing per token, so the line shows what those tokens would have
cost at the published rate. That comparison is what most tools show their
subscribers, and it is why the amount carries a `~` rather than a promise.

The number is calculated from the session history, so it survives `/load`: a
restored conversation shows the bill its own requests produced, not zero.
`/context` prints the same figure with the counts behind it — how many
requests were priced by the provider, how many came from the table, and how
many could not be priced.


## Notifications

Sigil separates in-app alerts from desktop notifications. In the terminal
client, transient alerts appear at the top right, above the composer, without
taking keyboard focus. Long stacks scroll within the upper part of the screen
instead of covering the input or question panel.

Desktop notifications announce a completed turn, a failed turn, or a batch of
questions waiting for your answer. Cancelling a turn does not send a completion
notification. Each notification identifies the project and conversation title;
these names can be visible on a locked screen. The message is generic and does
not include prompts, answers, question text, or exception details. If the session
has no title yet, the notification uses its short ID instead of message content.

- **Linux terminal:** Install `notify-send` (usually the `libnotify-bin` or
  `libnotify` package). Delivery needs a running desktop notification service.
- **macOS terminal:** Sigil uses `osascript`; allow notifications for the host
  application in system settings if required.
- **Other terminal platforms:** Native desktop delivery is not implemented.
  In-app alerts and question panels still work.
- **Web desktop:** Select **Enable alerts** in the header and grant notification
  permission. HTTPS or localhost and a supported browser are required. Select
  **Alerts on** to disable delivery again. This preference belongs to the
  browser. The page must remain open and connected; this is not background push
  delivery. Many mobile browsers require a service worker and cannot use this
  delivery path.

Terminal desktop notifications are enabled by default. Use `/alerts` to check
whether they are enabled and whether a local helper is available. Use
`/alerts on` or `/alerts off` to change the preference immediately, including
during a turn. The preference is saved in `~/.sigil/ui.json` (or under
`SIGIL_HOME`).

Terminal delivery runs on the machine hosting Sigil, not on an SSH client's
machine. It is best-effort and runs off the UI thread with a three-second
process timeout. Missing helpers, denied permissions, and system notification
policies can prevent an alert without interrupting the turn. Desktop delivery is
disabled during headless UI tests. Browser event replay and duplicate sequence
numbers do not resend old desktop alerts.

In the browser, answering a question, starting another turn, disabling alerts,
returning focus to the app, or signing out closes the obsolete desktop alert.
The button follows permission changes and preferences changed in another tab;
while a permission request is pending, it cannot open another request.

## Reading the chat

### Reasoning

The model's reasoning is **collapsed by default**. It is the part of the transcript
nobody reads twice, and expanded it buries the answer. The collapsed turn still
says it thought, how much, and how to see it:

```
✳ Reasoning · 42 lines  /reasoning-visibility on
```

`/reasoning-visibility on` expands every block in place — the conversation on
screen is not rebuilt, so a turn in flight keeps its pending deltas — and
`/reasoning-visibility off` collapses them again. The choice is stored in
`~/.sigil/ui.json` and applies to the next session too.

### Tool calls

A tool call and its result are one row, and the row is the summary:

```
  │ 4 tools · 610ms
  │ ✓ patch tests/test_roadmap.py  3 replacements · 24ms
  │ ✓ run ['pytest tests/test_roadmap.py -q']  9 passed in 0.45s · 450ms
  │ ✓ run ['pytest tests/test_per_…  1 failed, 8 passed in 0.61s · 610ms
  │ ↳ 1 failed
  │ ✓ read tests/test_roadmap.py  2 of 30 lines · 3ms
```

The gutter is what groups them: a run of calls reads as one column hanging under
the answer that caused it, instead of a list of unrelated lines that all start at
the left margin. Consecutive calls share no gap, so the column is continuous.

- **Two axes, deliberately apart.** The glyph says whether the *call* came back:
  `…` while it runs, `✓` when it returned, `✗` when it raised. The color of the
  summary says how it *went*, and a failing test run is a successful call with a
  red result — not a broken tool. Painting both with `✗` would make a red test
  look like a crash.
- **The right-hand side is the tool's own sentence**, not `str(result)`. Each tool
  declares how to summarize itself, because only the tool knows that
  `3 replacements` is the interesting fact about a patch and `bytes_before` is
  not. A tool that declares nothing shows nothing there; the row is still honest.
- The header over a run of two or more calls says what the step is and how far
  along it is: `⋯ 3 tools [1/3]` while it runs, `4 tools · 1 error · 610ms` when
  it is over. A batch of one call needs no header and does not get one.
- **The output is collapsed by default**, like the reasoning, and for the same
  reason: the row already says what came back. `… 12 lines` under a row that reads
  `9 passed in 0.45s` is a second line for nothing.
- **A failure is never collapsed.** A tool that raised shows its error, and a call
  whose result came back bad shows the first lines that explain it (`↳ 1 failed`).
  Hiding a failure behind a preference nobody has set yet would hide the one thing
  there is to act on.
- `/tools expanded` shows the output of every call, on screen and in the ones to
  come; `/tools collapsed` puts it back. The choice is stored in
  `~/.sigil/ui.json` and survives the next session.
- A dict of arguments is rendered as `key=value` pairs; a single argument is shown
  on its own, which is what makes `run(command='ls')` read as `run ls`.
- The row is composed to the width it has: the result sits flush right, the
  arguments are clipped with `…`, and when there is no room for both the result is
  dropped before the call is squeezed into an unreadable stub.
- A name shorter than the callable's: `write`, `patch`, `read`, `run`, `search`.
  An MCP tool keeps its full name, which is also what makes an unfamiliar tool
  look unfamiliar.
- Expanding keeps the output's leading whitespace, because a diff, a stack trace
  or a snippet of code is unreadable without it. Past six lines the block
  truncates with `… +N more`, and past 160 characters a long line truncates with
  an ellipsis. Enough to recognize the result, never enough to take over the
  screen.

## Themes

A theme is a JSON file with a name and a set of colors. Sigil ships two and reads
the ones installed in `~/.sigil/themes`, one file each:

```
~/.sigil/themes/
  my-theme.json
```

```json
{
  "name": "my-theme",
  "dark": true,
  "colors": {
    "primary": "#ff2a6d",
    "accent": "#05d9e8",
    "background": "#0d0221",
    "foreground": "#f8f8f2",
    "tool-border": "#4b2a6b",
    "reasoning": "#9d8ec7",
    "diff-added": "#01ffc3",
    "diff-removed": "#ff3864"
  }
}
```

Two kinds of key live under `colors`:

- The roles Textual knows — `primary`, `secondary`, `accent`, `warning`, `error`,
  `success`, `foreground`, `background`, `surface`, `panel`, `boost` — become the
  theme's palette. `primary` is required; the rest are inherited when omitted.
- Anything else becomes a CSS variable of that name. That is how a theme reaches
  the parts of Sigil the palette does not cover: `tool-border`, `tool-title`,
  `tool-output`, `reasoning`, `diff-added`, `diff-removed`, `sidebar-section` and
  `muted`.

A color that does not parse rejects the whole file instead of half-applying it: a
theme with two valid colors and one typo would paint an interface nobody designed.
A file that fails is reported in the chat at startup and skipped; the rest still
load. A user file with the same name as a builtin replaces it, which is the way to
keep the `dark` layout with your own colors.

`SIGIL_THEMES_DIR` moves the directory, so a theme can live in a dotfiles
repository instead of being copied around.

### Builtin themes

| Theme | What it is |
| --- | --- |
| `dark` | The default: a neutral dark surface with a cool accent, quiet enough not to compete with the content |
| `synthwave` | Neon on deep purple: hot pink, electric cyan and a violet sky. It exists to prove the system moves every surface and not just the accent |

### `/theme`

`/theme` opens the picker and applies each theme as the highlight moves, over the
real app: what you see while moving is what you get on Enter. `Esc` puts back the
theme that was active when the picker opened, and `Enter` applies the highlighted
one and stores it. `/theme --list` lists the installed themes with their origin and
reports the files that could not be read; `/theme <name>` applies one directly.

The picker re-reads the directory before opening, so a theme dropped in while
Sigil is running shows up without a restart.

## Context management

The full session history (the **log**) is append-only and is persisted as is.
What gets sent to the model on each request is a derived **view**: a deterministic
projection that does not mutate the log. That way it can be trimmed without
losing anything `/load` restores.

### Instructions per directory tree

`AGENTS.md` (or `CLAUDE.md` as a fallback) is resolved over the active directory
tree:

- The session root's `AGENTS.md` always applies.
- A subdirectory's applies only when the agent operates inside it.
- The order is parent → child: the child goes last and takes precedence.
- In the same directory, `AGENTS.md` wins over `CLAUDE.md`.

The block travels through the instructions channel (operator content), never as a
user message, and uses `on_change='append'`: when the scope widens mid-turn the
child text is appended instead of rewriting the prefix, which is what would break
the prompt cache.

### View strategies

| Strategy | What it does |
| --- | --- |
| `windowed` (default) | Drops reasoning, truncates old tool results and discards old turns if they do not fit in the window. |
| `compacting` | Also collapses old turns into a deterministic summary (`CompactionPart`), without calling the model. |
| `passthrough` | Sends the history as is. |

Recent turns stay intact: the model usually needs the exact details of the last
turn on the next one.

### Measurement

The side panel shows the **projected** number: the measured value of the last
request plus an estimate of what was added since then. The measured one describes
the past; the projected one is what matters to know whether the next request fits
in the window. `/context` shows both, the budget, the breakdown per turn and the
cost of the session so far.

### Agent debug view (`alt+d`)

`alt+d` replaces the chat with content-free diagnostics for the current execution.
It shows context estimates, reported cache reads and writes, per-turn usage,
recorded maintenance events, and the next request projection. Dynamic instruction
blocks retain their yellow highlighting. `/debug --text` returns the same numeric
report without prompt, tool payload, or conversation previews.

Maintenance reports reasoning targets/ceilings, partial or unknown encrypted
sizes, calls by LOD, separate argument/result reductions, exceptions, and skill
resource states. Historical records and projected events are labeled separately.
Cache writes are not hits; missing cache reporting is not evidence of zero reuse.

The shared pipeline preserves recent reasoning across user turns and reduces it
in batches (default ceiling 20, target 10). External tool inputs and outputs use
aggregate 2,000/500/100-character LOD targets; own tools declare semantic policies.
If the protected request still exceeds its estimated budget, Sigil stops before
calling the provider with an actionable error.

See [context maintenance and calibration](docs/context.md) for recovery rules,
known estimation limits, and the read-only simulation command.

### Configuration

Settings are read once, at startup, by `load_settings()` in the composition root.
Nothing reads the environment at import time, so a test (or a stale `.env`) cannot
change behaviour behind your back.

| Variable | Default | What it does |
| --- | --- | --- |
| `SIGIL_MAX_STEPS` | `256` | Model calls per turn when the profile declares no `max_steps`; running out ends it with a last tool-less answer. |
| `SIGIL_MODEL_MAX_ATTEMPTS` | `5` | Attempts per model request on transient failures. |
| `SIGIL_HOME` | `~/.sigil` | Where sessions, roadmaps and provider credentials live. |
| `SIGIL_CONTEXT_STRATEGY` | `windowed` | `windowed`, `compacting` or `passthrough`. |
| `SIGIL_CONTEXT_REASONING_STEPS` | `10` | Reasoning step target across turns; ceiling is twice the target. |
| `SIGIL_CONTEXT_REASONING_MAX_PERCENT` | `5` | Reasoning size target; ceiling is twice the target, at least 10%. |
| `SIGIL_CONTEXT_IMAGE_ROUNDS` | `3` | Rounds an image stays loaded. Older ones leave their id behind. |
| `SIGIL_CONTEXT_IMAGE_MAX_PERCENT` | `10` | Ceiling for the pixels that stay, as a percentage of the model window. |
| `SIGIL_CONTEXT_KEEP_RECENT_TURNS` | `3` | Turns kept intact before trimming. |
| `SIGIL_CONTEXT_OUTPUT_RESERVE` | `8000` | Tokens reserved for the response. |
| `SIGIL_WORKTREES_DIR` | `<SIGIL_HOME>/worktrees` | Where `/new --worktree` creates the worktrees. |
| `SIGIL_MCP_SHUTDOWN_TIMEOUT` | `0.2` | Seconds a stdio MCP server has to exit on EOF when quitting. |

A value that cannot be parsed fails loudly at startup instead of silently falling
back to the default. `.env.example` lists all of them.

The full design and the verifications against the provider are in
[`proposals/context-management.md`](proposals/context-management.md). The provider
layer plan is in [`proposals/model-providers.md`](proposals/model-providers.md).

## Development

```bash
./sigil              # run Sigil from here, with this directory as the workspace
uv run pytest        # tests
uv run ruff check .  # lint
uv run ruff format   # format
```

`./sigil` and `uv run ./main.py` are the same program; the launcher is the one that
works from another project's directory (see [Running it](#running-it)).

### The web client

`web/` is a React + TypeScript client (Tailwind for styles, Zustand for state) and
its **built output lives in the repository**: `web/dist` is committed, so the web
server runs without Node. Node is needed to *change* the client, never to use it.

```bash
cd web
npm ci               # once
npm run dev          # http://localhost:5173, proxying /api and /events to the server
npm run build        # tsc + vite build, rewrites web/dist and its build.json
npm run lint         # eslint, type-aware
npm run format       # prettier (also sorts Tailwind classes)
```

The bundle carries the hash of the sources that produced it (`web/dist/build.json`).
`python -m src.server.webbuild` recomputes that hash and exits non-zero when the
committed bundle no longer matches `web/src`, which is the only failure mode that
committing `dist/` introduces. The recipe exists twice on purpose — Python in
`src/server/webbuild.py`, Node in `web/scripts/source-hash.mjs` — because the server
must answer without Node; `tests/test_web_dist.py` compares both so they cannot drift.

### The pre-commit hook

Hooks are tracked in `.githooks/`, and `core.hooksPath` — which is per-clone
configuration that git cannot ship — is set automatically by whichever happens
first:

| Entry point | When it fires |
| --- | --- |
| `./sigil` | on launch, for a clone that has never run Sigil |
| `npm ci` / `npm install` in `web/` | through the `prepare` script |
| `uv run python scripts/install_hooks.py` | by hand, with the full report |

All three are idempotent and silent when there is nothing to do. `SIGIL_NO_HOOKS=1`
opts out. A clone that is never launched, never gets `npm ci`, and never runs the
installer still has no hooks — the checkout is the only thing git can share, and the
gate lives here because a hook in `.git/hooks` would not travel at all.

On every commit, `scripts/pre_commit.py` does four things, in this order:

1. `ruff format` and `ruff check --fix` on the staged Python files, then re-stages
   what it rewrote.
2. `prettier --write` and `eslint --fix` on the staged files under `web/`.
3. Rebuilds `web/dist` when anything under `web/` (other than the bundle itself)
   changed, and stages the result — so a commit that touches the client carries the
   bundle that commit produces.
4. Refuses the commit when a rule cannot be fixed automatically, naming the file,
   the rule and the way out.

| Variable | Effect |
| --- | --- |
| `SKIP_SIGIL_HOOK=1` | skips the whole gate (rebase, WIP commit) |
| `SKIP_PY_LINT=1` | skips ruff |
| `SKIP_WEB_LINT=1` | skips prettier and eslint |
| `SKIP_WEB_BUILD=1` | skips the rebuild — the commit will carry a stale bundle |
| `SIGIL_NO_HOOKS=1` | leaves `core.hooksPath` alone: the hooks are never enabled |

`scripts/check_hook.sh` exercises the gate end to end in a throwaway clone (real git,
real ruff, real npm, real rebuild), because a hook that stops working fails silently:
nobody notices until the committed bundle is wrong.

