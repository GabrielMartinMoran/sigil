# Sigil

Terminal agent built with [pydantic-ai](https://ai.pydantic.dev/) and
[Textual](https://textual.textualize.io/).

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

### Limitations

Sampling, elicitation and roots are not announced to the server. They require the
client to lend the model or the UI mid-turn, and that opens recursion and
interactive questions inside a tool. A server that depends on those capabilities
will fail when asking for them.

## Built-in tools

Besides the skills and the MCP servers, Sigil registers a set of its own tools.
They all go through the same logger, so every call shows up in the chat with its
duration and its arguments.

| Tool | What it does |
| --- | --- |
| `run` | Runs a shell command with a timeout and an anti-self-launch guard |
| `read_file` | Reads a file, or a line range, with a byte limit |
| `write_file` | Creates a file, or replaces or appends its content |
| `patch_file` | Edits a file by exact text or by line range |
| `web_search` | Searches the web and returns each result's title, URL and snippet |
| `read_url` | Extracts a page as Markdown, text or HTML |
| `ask_user` | Asks the user with options, inside the UI |
| `update_roadmap` | Publishes and updates the roadmap of the current task |

A tool error does not cut the turn: it goes back to the model as a failed result
(`outcome: failed`) and the agent decides how to proceed. If a `patch_file`
`old_string` does not appear, the model reads the error and corrects the call
instead of handing control back to you mid-task.

### Files

`read_file`, `write_file` and `patch_file` work inside the workspace: a path
outside it is rejected with an error, it is not resolved silently.

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

`ask_user` opens a modal with 2 or 3 options per question —the recommended one
appears marked— and a free field to type another answer. The agent uses it when
the requirements are ambiguous or an action needs explicit authorization. If you
cancel, the tool returns `cancelled: true` and the turn keeps going without an
answer.

### Roadmap

`update_roadmap` maintains the task list of the left panel. Each item stays in
`pending`, `in_progress`, `done` or `blocked`.

The panel shows a single list in plan order, without grouping by status. The icon
and the color are enough to read it at a glance:

| Status | Icon | Style |
| --- | --- | --- |
| `done` | `✓` | green, strike |
| `in_progress` | `▶` | blue |
| `pending` | `○` | white |
| `blocked` | `!` | yellow |

The roadmap belongs to the session, not to the directory: each session starts
with an empty list, advances with it and recovers it when loading it with
`/load`. The state is saved in
`~/.sigil/roadmaps/<cwd-hash>/<session-id>.json`. If the write fails, the list
keeps working in memory and the error stays in `persistence_error`.

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
limited to the active workspace.

What a new worktree does **not** get, because it is not in the commit:

- uncommitted changes of the current workspace,
- ignored files: `.env`, `.venv`, `.sigil/config.json`.

Sigil says so in the chat when the worktree is created. The MCP configuration is
read **once**, at startup, from the directory Sigil was launched in: a worktree
usually has no `.sigil/config.json` of its own, and reloading it per workspace
would leave the session without MCP servers.

### Sessions and workspaces

A session belongs to the workspace it was created in, and each workspace keeps its
own session folder (`~/.sigil/sessions/<cwd-hash>/`). `/load` lists the sessions
of **every** workspace of the repository, labelled:

| Label | Workspace |
| --- | --- |
| `main` | the repository's main worktree |
| `<name>` | a linked worktree (`fix-login`) |

```
● main          1a2b3c4d  fix the login form
  fix-login     9f8e7d6c  try the new parser
```

`●` marks the workspace in use. Choosing a session of another worktree switches to
it (the same rebind as `/new --worktree`) and then loads the session: a session is
never opened against a tree it does not belong to.

Starting Sigil in a subdirectory of a worktree is fine: the subdirectory is its own
workspace for sessions, and it shows the label of the worktree that contains it.
The side panel shows the active workspace above the context section, and
`/context` prints its root and branch.

A session whose worktree was `git worktree remove`d stops being listed: there is no
workspace left to open it against. The file stays on disk.

## Turn limits

A turn is cut for a single reason: `SIGIL_MAX_STEPS` (default `256`). Every model
response and every tool call counts as one step; exceeding the limit aborts the
turn with a notice in the chat and what was completed stays in the session.

pydantic-ai brings its own cap of 50 requests per run. Sigil disables it
(`request_limit=None`) so the only limit is the step one: otherwise a long turn
died with a `UsageLimitExceeded` nobody configured, before reaching the real
limit.

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
and a work OpenAI org — and switch between them with `/provider`.

```
/provider                 open the list: activate, add, rename, remove
/model                    pick a model for the active account
/effort [level]           open the effort picker, or set a level directly
```

| Shortcut | What it opens |
| --- | --- |
| `ctrl+t` | the reasoning-effort picker |
| `ctrl+m` | the model picker |
| `alt+d` | the agent debug view |

`ctrl+m` and `ctrl+t` are read by the prompt itself rather than bound globally, so
Enter keeps working: on a terminal without the Kitty keyboard protocol, Ctrl+M and
Enter are the same byte (CR, `0x0D`). Textual enables the protocol's
"disambiguate escape codes" flag, so a capable terminal sends a distinct sequence
for Ctrl+M and both shortcuts work; a legacy terminal sends Enter and the message
is sent, exactly as before. Nothing is swallowed either way.

In every list: `↑`/`↓` move, `PgUp`/`PgDn` jump, `Enter` chooses, `Tab`
completes (writes the highlighted value into the input without committing), `Esc`
goes back. In the command menu, `Tab` completes the highlighted command and adds
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
~/.sigil/providers/
  accounts.json    0644  aliases, provider family, product, sign-in method
  credentials.json 0600  the keys and OAuth tokens
  state.json       0644  last model used, OAuth client registrations
```

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

## Commands

Commands are typed in the prompt and start with `/`. The menu filters them as you
type.

| Command | What it does |
| --- | --- |
| `/new [--worktree <name>]` | Starts a new session, with its empty roadmap; `--worktree` creates a git worktree and works there |
| `/load` | Loads a saved session and its roadmap, switching workspace if it belongs to another worktree |
| `/provider` | Manage provider accounts: add, rename, remove, activate |
| `/model` | Picks the model for the active account |
| `/effort [level]` | Opens the effort picker; with a level, sets it directly |
| `/sidebar [on\|off]` | Shows or hides the sidebar; without an argument, toggles |
| `/mcp` | Queries the state of the MCP servers |
| `/context` | Shows the breakdown of the used context |
| `/debug [on\|off\|--text]` | Shows or hides the agent debug view; without an argument, toggles |
| `/help` | Lists the available commands |
| `/quit` | Exits Sigil |

The sidebar starts visible. Hiding it does not erase the roadmap: the agent keeps
updating it and it comes back with `/sidebar on`.

### Sidebar

Above the roadmap, the session panel shows:

- **Model**: the active account alias, its provider family, the model and the
  reasoning level in effect. Before anything is configured it says `not
  configured — /provider`, which is the first thing to do on a fresh install.
- **Workspace**: the tree the agent is working in: `main` for the repository's
  main worktree, or the name of a linked one. It also shows the branch, and it is
  the reason two sessions of the same repository are told apart.
- **Context**: the percentage of the window the next request is going to take,
  with the pair it comes from in parentheses. It is hidden until a model is
  configured, and it colours green, yellow and red across 70% and 90%.
- **Loaded skills**: the skills the agent loaded in this session, in load order.
  It is the quick diagnostic of whether it is following the protocol.
- **MCP**: the count goes inline with the title, in parentheses. Below, each
  server is listed: `●` in green for the ones that work, `✗` in red for the ones
  that do not. With no configured servers, the section does not appear.

The interface, the code and the documentation are all in English.

```
Context
 66% (132K / 200K)
```

The tokens are compacted so the pair fits in a 34-column panel: `K` for
thousands and `M` for millions, no decimals once the number is `>= 100K` and
never for millions. Below 100K a single decimal survives when it says something
—`12.4K`, `5.5K`— and disappears when it would read `.0`.

```
MCP (3, 1 ✗)
 ● github
 ● playwright
 ● filesystem
 ✗ broken
```

The MCP count refreshes on startup (in a worker, without blocking the UI) and
every time you run `/mcp`.

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
in the window. `/context` shows both, the budget and the breakdown per turn.

### Agent debug view (`alt+d`)

`alt+d` replaces the chat with **the agent side** of the conversation: not what
the user read, but what travels in the request. It answers two questions the chat
cannot: what the agent has in context right now, and how much of that comes from
cache.

```
 DEBUG   agent side of the conversation   alt+d to close
  model ~anthropic/claude-sonnet-4  effort high  window 200,000
  log 141,209 tok → agent view 38,412 tok  (−102,797 tok, 73%)
  cache 92% hit  cached 24,800  uncached 1,200  writes 13,800  cost $0.4213
```

From top to bottom:

- **Fixed header**: model, effort, window, and the pair that matters — tokens of
  the persisted log against tokens the agent actually receives, with what was
  left out. Below, the prompt cache summary.
- **Fixed cost per request**: every instruction block (marked `static` or
  `dynamic`) and every tool schema, with their tokens. The order of the blocks is
  the order in which they travel: the `dynamic` ones that are recomputed per
  request are the candidates to break the cacheable prefix.
- **Prompt cache per request**: one row per request with `input`, `read`,
  `written`, `uncached` and the hit ratio. `input` is inclusive —it includes what
  was read from and written to cache—, so `uncached` is the expensive number:
  input the provider processed from scratch and that could have been reused if
  the prefix had been stable. An `uncached` that does not go down after the first
  request is the sign that something invalidates the prefix.
- **Applied context strategy**: which levers the view used (reasoning dropped,
  tool results truncated, turns compacted or discarded).
- **Agent view**: the messages the model receives on the next request, message by
  message, part by part, with tokens and bounded previews.
- **Session log**: the same messages as they are persisted and shown in the chat,
  in grey. What is in one list and not in the other is exactly what the UI was not
  telling you.

The input stays active with the view open: the view refreshes on every step of
the turn, so you can launch a prompt and watch the cost grow live. Refreshes are
coalesced per 150 ms window so as not to re-project the history on every stream
event.

`/debug --text` dumps the same report as plain text into the chat, to read it
there or copy it.

### Configuration

Settings are read once, at startup, by `load_settings()` in the composition root.
Nothing reads the environment at import time, so a test (or a stale `.env`) cannot
change behaviour behind your back.

| Variable | Default | What it does |
| --- | --- | --- |
| `SIGIL_MAX_STEPS` | `256` | Hard step limit per turn. |
| `SIGIL_MODEL_MAX_ATTEMPTS` | `5` | Attempts per model request on transient failures. |
| `SIGIL_HOME` | `~/.sigil` | Where sessions, roadmaps and provider credentials live. |
| `SIGIL_CONTEXT_STRATEGY` | `windowed` | `windowed`, `compacting` or `passthrough`. |
| `SIGIL_CONTEXT_KEEP_THINKING` | empty | If turned on, the view does not drop reasoning. |
| `SIGIL_CONTEXT_KEEP_RECENT_TURNS` | `3` | Turns kept intact before trimming. |
| `SIGIL_CONTEXT_MAX_TOOL_RESULT_CHARS` | `4000` | Cap of an old tool result. |
| `SIGIL_CONTEXT_OUTPUT_RESERVE` | `8000` | Tokens reserved for the response. |
| `SIGIL_WORKTREES_DIR` | `<SIGIL_HOME>/worktrees` | Where `/new --worktree` creates the worktrees. |

A value that cannot be parsed fails loudly at startup instead of silently falling
back to the default. `.env.example` lists all of them.

The full design and the verifications against the provider are in
[`proposals/context-management.md`](proposals/context-management.md). The provider
layer plan is in [`proposals/model-providers.md`](proposals/model-providers.md).

## Development

```bash
uv run ./main.py     # run Sigil
uv run pytest        # tests
uv run ruff check .  # lint
```
