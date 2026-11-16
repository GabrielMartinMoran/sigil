# Architecture

Sigil separates the engine and its persistent state from terminal and browser
clients. The local service owns execution and publishes state and events; a
client presents them and sends user actions back to the service.

## Build a mental model

Read the vocabulary before changing identities or persistence:

1. [Domain model](domain.md): workspaces, sessions, executions, turns, and
   messages.
2. [Storage](storage.md): SQLite schema, write paths, migrations, and historical
   rationale.
3. [Service runtime](service-runtime.md): process ownership, session ownership,
   durable work, and idle shutdown.

The [original core-boundary proposal](../design/core-boundary.md) records the
starting design. Its claim that orchestration still needs to move out of the TUI
is historical, not a description of today's default launcher.

## Follow a request through the system

The implementation records are divided by boundary so you can inspect one part
without reading a single service-design monolith:

| Boundary                                              | Record                                      |
| ----------------------------------------------------- | ------------------------------------------- |
| Terminal/API/browser startup surfaces                 | [Service surfaces](service-surfaces.md)     |
| Engine and durable work ownership                     | [Service runtime](service-runtime.md)       |
| Discovering and authenticating a local service        | [Local endpoint](local-endpoint.md)         |
| Finding or starting the service process               | [Service launcher](service-launcher.md)     |
| Shared commands, routes, refusals, and model defaults | [Service commands](service-commands.md)     |
| HTTP transport, event decoding, and typed failures    | [Terminal transport](terminal-transport.md) |
| Terminal state, questions, reconnects, and rendering  | [Terminal client](terminal-client.md)       |
| Agent-tree projections, actions, and report delivery  | [Agent tree](agent-tree.md)                 |

## Related contracts

Use [internals](../internals/README.md) for context, rollback, tools, and
profile behavior. To verify a change, follow the
[component testing guide](../development/testing.md).

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
