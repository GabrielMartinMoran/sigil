# Built-in tools and execution contracts

This reference covers registration, concurrency, permissions, and execution
boundaries. For installing extensions, use the
[extension author guide](../development/local-tools.md); for access decisions,
start with the [safety guide](../../usage/guides/safety.md).

## Automatic tool logging

Tools are registered with `ToolRegistrar` instead of `agent.tool_plain` /
`agent.tool`. The wrapper logs every call (`start`, `success`, `error`, duration
and args) and the UI reflects it in the chat. It works the same for sync, async
and `RunContext` tools.

```python
from src.core.tooling.logging import ToolLoggerHub
from src.core.tooling.registrar import ToolRegistrar

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
from src.core.tooling.concurrency import ConcurrencyContext


def no_same_key(ctx: ConcurrencyContext) -> str | None:
    for sibling in ctx.siblings:
        if sibling.tool_name == ctx.tool_name and sibling.args.get('key') == ctx.args.get('key'):
            return f'{ctx.tool_name} already ran for key {ctx.args["key"]!r}'
    return None


@registrar.tool(concurrency=no_same_key)
def my_tool(key: str) -> str: ...
```

The guard is evaluated in invocation order: the first call of a conflicting
group always goes through and the following ones fail with a `ToolConflict`. The
model sees it as a failed tool result and can re-emit the call in a separate
step. The rest of the calls keep running in parallel, with no global barriers.

A direct call from code —without a `RunContext`— runs anyway, without a guard:
there are no siblings to compare against.

### What a tool is, for the permissions

A tool can declare the group it belongs to and the argument it acts on, which is
what lets a profile rule on `write` instead of listing every tool that writes,
and on the path instead of on the tool:

```python
from src.core.tooling.permission import ResourceKind, ToolGroup,
ToolPermission

_READ = ToolPermission(groups=(ToolGroup.READ,), resource='path',
kind=ResourceKind.PATH)


@registrar.tool(permission=_READ) def read_file(path: str) -> FileContent: ...
```

The declaration travels as tool metadata: nothing in the signature changes, and
a tool that does not declare one is matched by its name alone — which is what
MCP tools and `agent.tool_plain` do.

## Built-in tools

Besides the skills and the MCP servers, Sigil registers a set of its own tools.
They all go through the same logger, so every call shows up in the chat with its
duration and its arguments.

| Tool                                                                                                | Group     | What it does                                                                           |
| --------------------------------------------------------------------------------------------------- | --------- | -------------------------------------------------------------------------------------- |
| `shell`                                                                                             | `run`     | Runs shell commands with a timeout and an anti-self-launch guard                       |
| `execute_code`                                                                                      | `run`     | Composes permitted tools with async Python in an experimental Linux sandbox            |
| `read_file`                                                                                         | `read`    | Reads a file, or a line range, with a byte limit                                       |
| `write_file`                                                                                        | `write`   | Creates or edits one file with ordered operations                                      |
| `web_search`                                                                                        | `web`     | Searches the web and returns each result's title, URL and snippet                      |
| `read_url`                                                                                          | `web`     | Extracts a page as Markdown, text or HTML                                              |
| `list_skills`, `load_skill`, `read_skill_resource`, `run_skill_script`                              | `skills`  | Discovers, loads and reads Agent Skills; a bundled script also counts as `run`         |
| `list_mcp_servers`, `list_mcp_resources`, `read_mcp_resource`, `list_mcp_prompts`, `get_mcp_prompt` | `mcp`     | The meta-tools over MCP servers, resources and prompts                                 |
| `ask_user`                                                                                          | `ask`     | Asks the user with options, inside the UI                                              |
| `update_roadmap`                                                                                    | `roadmap` | Publishes and updates the roadmap of the current task                                  |
| `summon`                                                                                            | `summon`  | Starts a subagent, or resumes one by id. See [Subagents](agent-execution.md#subagents) |
| `send_message`                                                                                      | `summon`  | An instruction to a child, or a notice to a parent or a sibling                        |
| `kill_subagent`                                                                                     | `summon`  | Stops a subagent and everything it summoned                                            |
| `list_agents`                                                                                       | `summon`  | The ids, names, states and parents of the agents of the session                        |

The group is what a profile's `permissions` rules on, together with the tool
name: `permissions: {write: deny}` closes `write_file` in one line. A tool
declares its group once, next to its definition, and
`tests/core/test_tool_groups.py` fails if the declaration and the documented
table drift apart.

A tool error does not cut the turn: it goes back to the model as a failed result
(`outcome: failed`) and the agent decides how to proceed. If a `write_file`
replacement's `old` text does not appear, the model reads the error and corrects
the call instead of handing control back to you mid-task.

The one deliberate exception is a cancelled `ask_user`: that is not a tool
failure but your decision to stop. It ends the turn and returns control to you,
with the partial history kept and the unanswered call closed out.

### Experimental Python execution

`execute_code` runs Python with top-level `await` and exposes `read_file`,
`write_file`, `web_search`, and `read_url` through `tools`. Each nested call
uses its normal profile permissions. For example:

```python
result = await tools.read_file(path="README.md")
print(result["content"])
```

The runtime requires Linux, bubblewrap, cgroup v2, and a working systemd user
manager with delegation support. A trusted supervisor places the stopped payload
in a delegated child cgroup before starting it. The supervisor keeps that cgroup
observable until the host confirms `populated=0`. On cancellation or host
disconnection, it terminates the payload using `cgroup.kill`. A separate control
channel carries readiness and cleanup messages, not user output. If setup or
cleanup verification fails, execution fails; there is no host fallback. The
call's `timeout_seconds` defaults to 120 and accepts values from 1 to 120.
Sandbox concurrency is limited to two calls per Sigil process, not across
separate processes. System runtime directories are mounted read-only. This is
experimental isolation, not a verified security boundary.

Cancellation cannot undo completed effects. A synchronous nested tool already
running in a thread may continue after cancellation. Writes are serialized
within one execution; concurrent executions and other processes aren't covered
by that lock.

### Files

`read_file` and `write_file` work inside the workspace and inside the OS
temporary directory: any other path is rejected with an error, it is not
resolved silently.

The temporary directory is allowed because scratch files are what it is for. A
command writes one, the model reads it back, and refusing the path would only
push the work into the shell. The directory is resolved on every call, so
`TMPDIR` is honoured; on POSIX, `/tmp` and `/var/tmp` are accepted as well,
because a redirect to `/tmp` does not read `TMPDIR` either.

The allowance is the temporary tree itself: `..` does not climb out of it, and a
symlink is followed before the check, so it cannot be used to reach a file
outside. A profile still rules on these paths like any other, so
`write: {deny: ['/tmp/*']}` closes the scratch area for an agent that must not
have one.

`write_file` accepts a path and an ordered list of operations. It applies all
operations in memory before writing the result; if an operation fails, no edit
is written. Available operations are:

- `write` and `append`, each with `content`.
- `replace`, with `old` and `new`; the match must be unique unless `replace_all`
  is true. Set `new` to an empty string to delete text.
- `replace_lines`, with `start_line`, `end_line`, `new`, and optional
  `expected_text`. A trailing newline is ignored when checking expected text.

Line replacements preserve the file's line endings. For existing files, atomic
replacement attempts to preserve ownership, mode, and extended attributes,
including ACLs stored as extended attributes. If it can't preserve them, the
edit fails without replacing the original. Platforms without extended-attribute
support in Python cannot edit existing files. Files observed with multiple hard
links are rejected. These checks don't protect against concurrent external
changes; atomic replacement prevents partial writes, not lost updates.

`write_file` declares a concurrency guard: two calls that write the same file in
the same response clash. The first goes through and the second fails with a
message asking the model to re-emit it in a separate step. Editing different
files keeps running in parallel, and reading a file never clashes with editing
it.

### Web

`web_search` uses [ddgs](https://pypi.org/project/ddgs/) and needs no API key;
it returns up to 20 results per query. `read_url` converts the page to Markdown
by default (`text_markdown`) and clips the result at 40,000 characters. Binaries
come back in base64.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
