# MCP initialization and shutdown

MCP discovery and toolset initialization belong to the runtime, not to a browser
page. The default terminal connects to the local service; service startup and
session composition determine when its integrations become ready. See
[Service runtime](../architecture/service-runtime.md).

## Preserve startup responsiveness

The original in-process terminal deferred building the MCP layer until after the
first paint and used a worker thread to avoid importing the MCP SDK on the UI
thread. During that initialization, an empty toolset placeholder meant an early
turn could have no MCP tools yet. This is historical startup rationale, not a
guarantee that every service-connected client uses that exact sequence.

## Bound process shutdown

`SIGIL_MCP_SHUTDOWN_TIMEOUT` defaults to 0.2 seconds and bounds how long a stdio
server gets to exit after EOF. Servers that ignore shutdown are signalled rather
than intentionally left running. `keep_alive` controls reuse between turns;
startup and process ownership are separate from that setting.

Use the [MCP user guide](../../usage/configuration/mcp.md) for transport fields,
server state, error handling, and session enable/disable controls.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
