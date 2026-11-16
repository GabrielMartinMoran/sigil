# Permissions and safety

Sigil can edit files, run commands, and call connected services. Its permissions
help control tool access, but they aren't a security sandbox or a guarantee that
every model will follow its instructions.

## Know the default file boundary

Built-in file tools allow the active workspace and OS temporary directories.
Other paths are denied unless outside-workspace access is enabled for the
session:

| Command                 | Action                                              |
| ----------------------- | --------------------------------------------------- |
| `/external-files`       | Show the current state                              |
| `/external-files allow` | Allow outside-workspace file tools for this session |
| `/external-files deny`  | Restore the restriction                             |

The choice persists with the session. It controls file tools, **not shell
commands, MCP tools, or imported extension code**. An allowed shell command can
reach what its operating-system user can reach.

## Distinguish instructions from enforcement

The built-in Wizard is instructed to investigate, propose a plan, and wait for
approval before changing files. Scout is instructed to use its shell read-only.
These are workflow agreements, not an operating-system approval lock or a
read-only process sandbox. Custom profiles can change the instructions.

Profile permission rules can remove tools or refuse matching calls. Resource
patterns are evaluated on tool arguments, not on every possible effect of a
command. Prefer narrowly allowed commands when writing restricted profiles;
don't rely on a short denylist to recognize every dangerous shell operation. See
[Agent permissions](../customization/agents.md#permissions).

## Trust integrations deliberately

MCP servers, skill scripts, and local extensions can perform consequential work.
Project Python extensions require approval before import; global extensions are
treated as trusted installed code. An extension runs with your user permissions,
outside any promise of plugin isolation.

Experimental Python execution has separate Linux isolation requirements and
fails if it cannot establish its runtime. It isn't a verified security boundary.
Cancellation also cannot roll back nested tool effects that already completed.
See the
[technical execution contract](../../technical/internals/tooling.md#experimental-python-execution).

## Protect data and external effects

State is saved locally, but provider requests leave the machine and remote tools
can send data elsewhere. Credentials are private files, not an OS keyring. Back
up your data securely and never share credential or endpoint-token files.

A stopped turn keeps completed changes. A [rollback](rollback.md) can restore
covered workspace files, but it cannot undo external actions or ignored files.
Inspect commands and integration permissions before authorizing high-impact
work.

---

[Documentation index](../../README.md) · [User documentation](../README.md)
