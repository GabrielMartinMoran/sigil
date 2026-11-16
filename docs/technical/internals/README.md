# Engine and client internals

These references explain contracts that user guides deliberately leave out.
Start with the corresponding user workflow when you need to check the expected
observable behavior.

## Engine behavior

Choose the contract you are changing:

- [Context projection](context.md): budgets, compaction, persisted decisions,
  and estimates.
- [Project instruction scope](instructions.md): directory rules and prompt
  placement.
- [Agent profiles](agent-profiles.md): selection, discovery, and built-in
  working agreements.
- [Agent execution](agent-execution.md): subagents, permissions, messaging, and
  per-node state.
- [Turn lifecycle](turn-lifecycle.md): steering, cancellation, limits, and
  history repair.
- [Rollback](rollback.md): conversation rewind, Git snapshots, and restore
  ordering.

## Tool and presentation contracts

Keep runtime behavior distinct from how a client displays it:

- [Tool registration and execution](tooling.md): logging, concurrency,
  permissions, file operations, web tools, and experimental Python execution.
- [MCP lifecycle](mcp-lifecycle.md): initialization and shutdown behavior.
- [Terminal interface](terminal-interface.md): questions, images, tool
  rendering, sidebar, roadmap, and cost presentation.

For a new tool, use the
[local extension author guide](../development/local-tools.md). For day-to-day
operation, return to [user guides](../../usage/guides/README.md).

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
