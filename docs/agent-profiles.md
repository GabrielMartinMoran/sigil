# Session agent profiles

A session's effective agent profile controls its instructions and tool
permissions. The service reports that profile to connected clients; a displayed
name isn't a separate choice from the profile used to execute a turn.

## Built-in working agreements

The built-in profiles separate lead-agent responsibility from research. Their
instructions define a workflow; they don't add a runtime approval lock or a
shell sandbox. Project or global profiles with the same names take precedence.

### Wizard: approve the plan before implementation

Wizard announces its investigation, gathers read-only evidence, and presents a
plan covering scope, intended behavior, material risks, and verification. It
waits for your explicit approval before changing files or running state-changing
commands. This includes creating tests, writing documentation, installing
packages, and applying formatter fixes. A short change needs a short plan, not a
bypass of confirmation.

After approval, Wizard handles routine implementation decisions without asking
again. A material change in scope, compatibility, behavior, cost, or data risk
requires approval of the revised plan. A concrete plan you already explicitly
approved doesn't need another confirmation. Answers and drafts returned only in
chat don't need an implementation gate.

Wizard can launch multiple independent Scouts with `summon` in asynchronous mode.
It supplies self-contained briefs, retains their ids for follow-up, and
reconciles their findings. Wizard owns implementation and final verification;
Scout reports are evidence, never user approval or independent implementation QA.

### Scout: investigate without changing state

Scout researches repository behavior, bugs, architecture, tests, dependencies,
and external technical questions. Its report leads with the answer and includes
decisive file references or source URLs, uncertainty, and useful next steps.
Depth follows the question rather than a mandatory exhaustive audit or page cap.

Its permissions distinguish tool access from the read-only behavior contract:

- File reading, unrestricted shell commands, web research, skill reading, and
  MCP discovery and resource reading are available. Selected Codegraph, Context7,
  and Mind query tools are allowed when installed with matching tool names.
- Direct file writing, bundled skill execution, user questions, roadmap changes,
  and spawning subagents are denied. Scout can notify its parent of material
  findings or blockers.
- Shell use must remain read-only by instruction. Scout must not write through
  scripts or redirection, install packages, run autofixes, change Git state,
  start services, or execute tests and builds that create artifacts. Mind memory
  reads use `noPromote: true` to avoid promotion side effects.

Unrestricted shell access can technically mutate state. Tool-name permissions
and prompts aren't a security sandbox; the instruction is a behavior contract,
not a guarantee against every possible shell or remote-tool side effect. Custom
MCP prefixes may require adapting the selected tool-name patterns in a profile.

### Completion and verification boundaries

The base prompt prohibits orchestration with fixed sleeps followed by log,
exit-marker, process, or agent-list polling. Managed asynchronous subagents
notify their parent when complete; while waiting, the parent does independent
work or ends the turn. Without managed completion, commands stay under direct
execution-tool supervision. Execution limits are reported, not bypassed with
detached shell wrappers.

Wizard chooses the cheapest sufficient test level, improves relevant tests and
local code within scope, and avoids redundant suite growth. Independent file
writes and checks can run concurrently when their dependencies, state, and
resource use allow it. Final verification runs on a stable tree.

Deterministic tests validate profile parsing, permissions, and prompt rendering.
They don't prove a model will always follow approval or read-only instructions.
This change doesn't add live-model or other nondeterministic evaluations.

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

The implementation lives in [composition binding](../src/core/bootstrap.py),
[profile selection](../src/core/agents/profile/agent_registry.py), and
[server projections](../src/server/views.py). The
[profile scenarios](../specs/features/application/agent_profiles.feature)
record the behavioral contract.

See [the architecture](architecture.md) for service boundaries and
[storage](storage.md) for persisted sessions. Concurrent sessions and per-client
session routing are separate work; this fix doesn't implement them.
