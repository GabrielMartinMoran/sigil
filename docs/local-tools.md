# Local tool extensions

Local extensions add Python tools to Sigil without modifying its core or running
an MCP server. Put each extension in a directory containing `tools.py`, and mark
its public functions with `@tool`. Both terminal and web agents discover them
before their first model request.

## Design philosophy

Sigil owns discovery, activation, argument validation, agent permissions, tool
logging, and integration with context compaction. The extension owns its
implementation, external dependencies, subprocesses, and resource cleanup.

An extension is a directory, not necessarily an installable Python package. You
don't need `pyproject.toml` or `__init__.py`. Only decorated functions become
tools; ordinary functions remain implementation details. Relative imports such
as `from .helpers import parse` work within the extension directory.

This is a trusted-code extension mechanism, not a sandbox. Imported code,
lifecycle hooks, and tool callbacks run in Sigil's Python process with your
user permissions. Permission rules control agent invocations; they cannot
prevent Python code from accessing files, networks, or processes directly.

## Create your first extension

Create this directory in the workspace you want to extend:

```text
.sigil/tools/calculations/
└── tools.py
```

Add an asynchronous function to `tools.py`:

```python
from sigil_tools import tool


@tool
async def add_numbers(left: int, right: int) -> int:
    """Add two integers."""
    return left + right
```

Start Sigil and send a message. Before the first model request, Sigil asks
whether to load the project's Python extensions. Accept only if you trust the
code. Declining or canceling leaves project extensions unloaded. If the client
has no interactive question handler, project extensions are not trusted.

The model receives the tool name, docstring description, and a schema generated
from its type annotations. Functions without `@tool` are not advertised. You can
use `@tool(name="custom_name", description="Custom description")` to override the
defaults. Tools must use `async def`; synchronous tools fail during loading.

## Discovery and name conflicts

Sigil checks immediate subdirectories of these roots, in sorted order:

- `~/.sigil/tools`, or `$SIGIL_HOME/tools` when `SIGIL_HOME` is set.
- `<workspace>/.sigil/tools`, after interactive trust approval.

Global extensions are treated as installed, trusted user code. Discovery does
not recurse through nested directories. A directory without `tools.py` is not
an extension. No dependencies are installed automatically.

Tool names are function names unless overridden by the decorator; directory
names do not prefix them. A project tool overrides a global tool with the same
name, with a diagnostic. Duplicate names within the same scope are rejected.
Extensions cannot replace already registered built-in tools. A load or schema
error produces a diagnostic without disabling unrelated extensions or tools.

## Invocation context

To receive host context, annotate the first positional parameter with
`ToolContext`. Sigil injects it; the model does not see or fill that parameter.
Context is resolved on each invocation, not when the module is imported.

```python
from sigil_tools import ToolContext, tool


@tool
async def execution_identity(ctx: ToolContext) -> dict[str, str]:
    """Report the current workspace and agent identity."""
    return {
        "workspace": str(ctx.workspace),
        "agent_id": ctx.agent_id,
        "agent_name": ctx.agent_name,
        "session_id": ctx.session_id,
    }
```

The same extension instance serves the root agent and its subagents. The context
identifies the invoking node, while the session ID identifies their shared
conversation. Switching sessions does not reload the extension.

## Lifecycle hooks

Define these optional reserved functions in `tools.py` to prepare and release
resources. Both must be asynchronous and accept one `ExtensionContext` argument.

```python
from sigil_tools import ExtensionContext


async def on_load(ctx: ExtensionContext) -> None:
    """Validate external prerequisites before advertising tools."""
    executable = ctx.extension_dir / "worker"
    if not executable.is_file():
        raise RuntimeError("Install the worker executable before using this extension.")


async def on_unload(ctx: ExtensionContext) -> None:
    """Release resources, including any partially initialized resources."""
    # Close resources owned by this extension here.
```

`ExtensionContext` contains the absolute `extension_dir` and the workspace for
which the instance was activated. Keep import-time work minimal; do preparation
in `on_load` instead. Store shared resources in module state when necessary, and
make that state safe for concurrent invocations.

`on_load` runs once per activation, not once per agent. If it fails, that
extension's tools are not advertised. Sigil attempts `on_unload` even after
partial initialization, so cleanup must tolerate missing resources.

`on_unload` runs in reverse activation order during normal client shutdown. On a
workspace change, extensions are unloaded and the new workspace's extensions
are activated before the next model request. Existing calls finish before
resources are unloaded. Each lifecycle hook has a cooperative 10-second timeout;
blocking Python code or cancellation-suppressing code can defeat that timeout.
A cleanup exception is diagnosed without skipping other extensions.

There is no hot reload command: restart Sigil to pick up edits in the same
workspace. Cleanup is not guaranteed after a crash, forced termination, or
process kill. Don't rely on unload hooks for critical persistence. There is no
session-wide `on_compaction` hook; declare compaction on individual tools.

## Advanced tool declarations

The decorator integrates with the same registrar used by built-in tools. Its
optional fields are listed below.

| Field | Contract |
| --- | --- |
| `name`, `description` | Override the function name and docstring. |
| `permission` | A `ToolPermission` declaring groups and affected resources. |
| `compaction` | A `ToolCompactor` with input/output reducers and preserved keys. |
| `concurrency` | A synchronous function taking `ConcurrencyContext` and returning a conflict reason or `None`. |
| `describe` | A synchronous function taking argument mapping and result, returning `ToolSummary` or `None`. |
| `output` | A synchronous function taking a result and returning `ToolOutput` for human presentation. |
| `requires` | A model capability name or tuple of names required to advertise the tool. |

Only tool execution and lifecycle hooks are asynchronous. Reducers, resource
resolvers, concurrency guards, and presentation callbacks must be synchronous,
fast, and free of I/O. Compactors must be pure and deterministic; they can run
while reconstructing context rather than during tool execution.

### Permissions and resources

Declare the operation category and resource argument so existing profile rules
can evaluate the invocation. A declaration doesn't authorize a call by itself.

```python
from sigil_tools import ResourceKind, ToolPermission, tool


@tool(
    permission=ToolPermission(
        groups=("read",), resource="path", kind=ResourceKind.PATH
    ),
)
async def inspect_document(path: str) -> str:
    """Inspect a document using an external worker."""
    # Delegate to the extension's nonblocking implementation.
    return path
```

Profiles can use group rules and wildcard tool names, such as
`inspect_document*`. Current profile validation accepts external names through
wildcards rather than exact names. `resolve_resource`, when provided on
`ToolPermission`, takes the effective argument mapping and returns a tuple of
resource strings. It must be synchronous.

A tool without a permission declaration is still subject to name-based rules,
but it has no declared resource or operation groups. Declare resources honestly:
these controls are not an enforcement boundary for untrusted extension code.

### Compact arguments and results

Supply a domain-specific reducer when generic text truncation would lose
important structure. Without an explicit policy, Sigil uses its generic text
compactor for input and output.

```python
from typing import Any

from sigil_tools import ToolCompactor, compact_text, tool


def compact_matches(value: Any) -> Any:
    if not isinstance(value, dict):
        return compact_text(value)
    return {
        "total": value.get("total"),
        "matches": value.get("matches", [])[:5],
    }


@tool(
    compaction=ToolCompactor(
        input=compact_text,
        output=compact_matches,
        preserve=frozenset({"query"}),
    ),
)
async def find_matches(query: str) -> dict[str, Any]:
    """Find matching entries."""
    return {"total": 0, "matches": []}
```

Reducers receive normalized history values, not necessarily the original Python
return objects. Sigil retains the original when a reduced value is not smaller.
When an extension is unavailable after reopening a session, its custom policy
is unavailable too; don't depend on it to redact secrets or preserve data.
Never return secrets expecting later compaction to remove them.

## Dependencies and other languages

The imported extension code must work with Python's standard library and
`sigil_tools`. Don't rely on Sigil's internal dependencies merely because they
happen to be installed. Sigil doesn't create environments or install packages.

For additional libraries or another language, manage a worker executable,
script, or environment in the extension, and call it as a subprocess. Document
its installation and required versions. Validate prerequisites in `on_load`
rather than installing them silently when a project opens.

Use `asyncio.create_subprocess_exec`, explicit argument lists, bounded output,
and a timeout. Don't interpolate model input into shell command strings. Set
`cwd` explicitly; don't assume the process working directory still belongs to
this extension's workspace. If you need synchronous library I/O, consider
`asyncio.to_thread`; CPU-heavy work may need a separate process.

On cancellation or timeout, terminate and reap owned child processes. For a
persistent worker, also close it in `on_unload`. Calling `subprocess.run` inside
an `async def` still blocks Sigil's event loop: the keyword doesn't make blocking
code asynchronous.

## Next steps

Add a focused test for the extension's success and error paths, document its
external prerequisites, and review its permission and compaction declarations
before sharing it. See the [architecture guide](architecture.md) for the rest
of Sigil's engine boundaries.
