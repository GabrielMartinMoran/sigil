# Investigating latency in the terminal client

The audit found a real UI-thread blocker, but it did not establish the cause of
multi-second pauses between model steps. Git diff polling ran synchronously on
the Textual event loop. It now runs in a thread. The TUI also distinguishes
running tools from completed calls and records wall-clock timings for the most
recent turn.

## What the web extraction changes

The audit started at commit `be9a957`, which introduces the web client and moves
composition from `src/sigil.py` into `src/engine/bootstrap.py`.

The terminal path remains `SigilApp` → `TurnMixin.run_turn` → `Agent.iter`.
It does not pass through FastAPI, HTTP, the server event hub, SSE, or the browser
store. The web runtime is a separate driver of the same engine components, not
an intermediary for the terminal client. The web commit does not change
`src/ui/turn_mixin.py`.

There is still an architectural maintenance risk: the TUI and web runtime have
separate implementations of checkpointing, steering, cancellation, and turn
execution. That is not evidence of an extra transport delay in the TUI. Moving
both loops into a shared application runner remains a separate architectural
task; this audit does not attempt that migration.

## Measurements and their boundaries

These are local microbenchmarks, not measurements of a real provider request.
Each row reports the median and maximum of five consecutive calls in the audit
workspace. They do not establish production percentiles or end-to-end speedups.

| Operation | Median | Maximum |
| --- | ---: | ---: |
| Git diff summary of this repository, three subprocesses | 9.77 ms | 10.77 ms |
| Render six loaded skills, including resource discovery | 10.24 ms | 15.40 ms |
| SQLite tail save, 400 synthetic messages | 0.66 ms | 0.94 ms |
| SQLite tail save, 2,800 synthetic messages | 0.97 ms | 3.49 ms |
| Windowed context projection, 400 synthetic messages | 4.79 ms | 5.53 ms |
| Windowed context projection, 2,800 synthetic messages | 52.86 ms | 57.38 ms |

The history fixture uses four messages per turn: user prompt, tool call, tool
return, and final response. Each turn has a distinct run ID, an 8,000-character
tool result, and a repeated 26,000-character instruction block. SQLite timings
measure repeated saves after the initial insert, using a temporary database.
Context projection uses the default `WindowedStrategy` with a 200,000-token
window. Skill rendering uses the first six sorted entries of the discovered
registry, not necessarily the skills loaded in a user's slow session.

The old whole-session JSON rewrite is not the current persistence path:
`SessionStore.save` writes SQLite rows and a bounded history tail. The small
save times above do not rule out lock contention, slow disks, or expensive
subagent trees in other workloads.

## What was corrected

The fixes target verified behavior rather than attributing all latency to one
component.

- `ActivityMixin.refresh_diff` previously used an async Textual worker but called
  blocking Git commands directly. A worker is not a thread. The call now uses
  `asyncio.to_thread`, skips overlapping polls, and discards results belonging
  to a workspace that is no longer selected. Git was fast in this repository,
  but a slow Git read could previously stop input, stream handling, and spinners.
- Pending tool rows now show an animated one-cell spinner and `running · Ns`.
  Completed calls show a check or failure mark and their summaries. A declared
  bad result, such as a failed test run, now gets a failure mark even when the
  tool invocation itself returned successfully. Timers
  stop on completion, error, removal, and interrupted-turn finalization. On a
  narrow terminal, the elapsed label can be clipped away; the leading spinner
  remains the state indicator.
- Before each model-request node, the status bar says `Waiting for model`.
  Text and reasoning events continue to update the activity label afterward.

## Reading a slow turn

Use `Alt+D` for the debug view or `/debug --text` for a copyable report. The
report includes the most recent TUI turn's timing data; it is not persisted.

1. Inspect `ModelRequestNode` duration and its `first event` delay. A long delay
   places the wait inside model-request execution, not inside an executing tool.
2. Inspect `CallToolsNode` durations and individual tool-row durations. Parallel
   calls overlap, so adding their individual durations does not give elapsed
   batch time. A tools node can also contain final-output processing.
3. Inspect `outside nodes`. This is elapsed turn time not spent advancing graph
   nodes, including loop-body checkpoint work and turn setup/finalization.

A model-request node includes instruction preparation, context projection,
transport, provider processing, retries, streaming, and UI stream consumption.
Its `first event` is the first event delivered to the stream handler, not an HTTP
first-byte measurement. Checkpoints inside that handler are also part of the
node's duration. These numbers therefore do not isolate provider latency or
CPU time. The report retains at most 200 completed node records, preserves the
aggregate node time, and retains no prompt, tool argument, result, or credential
content.

## Next steps

Capture a turn that exhibits the reported pause and compare these timing
categories. If the model-node delay dominates, investigate request preparation,
provider first-event delay, retry notices, and prompt-cache usage before changing
the core boundaries. If tool-batch time dominates, investigate the specific
tool or MCP server. If outside-node time dominates, profile the local checkpoint
and UI paths with that workload.

No authenticated provider benchmark or real slow-session trace was collected in
this audit. The fixes are verified locally; the dominant source of the reported
multi-second pauses remains unconfirmed.

## Tool presentation in both clients

The tool transcript now follows the supplied compact prototype: aligned name and
argument columns, a right-hand outcome, progress badges, expandable output panels,
and collapsible completed groups. In the terminal, focus a row or group and press
Enter or Space, or click it. In the browser, the same controls are native buttons.
Failed rows remain visible when their group is collapsed.

The browser pairs persisted calls and results by call ID rather than drawing them
as separate rows. Live logging events carry an invocation ID so two concurrent
calls of the same tool do not all complete when one returns. A declared bad summary
also marks its live row as failed. The live stream uses the server's bounded
preview, not an unbounded copy of the result. Durations are not persisted by these changes. Historical rows now reconstruct
summaries through the engine's output formatters from the stored result; no timing
or test-success claim is invented when the result cannot establish it.

The group label does not claim parallel execution merely because calls are adjacent.
The browser labels a duration derived from individual calls as `slowest`, not batch
wall-clock time. The `all passed` badge requires every row to have an explicit good
summary. Terminal colors follow the selected theme; the browser uses violet
structure, green success, and red failure within the existing application palette.

## Declaring human-readable tool output

Tool summaries and expanded output now share a presentation boundary in the
engine. `src/engine/tooling/outputs.py` returns `ToolOutput(text, summary)`;
`output_defaults.py` declares the built-in formatters. The original tool result
still goes to the model unchanged. The TUI, browser events, and persisted web
transcript consume the human view rather than calling `str()` on a result.

The built-in formatters handle process stdout/stderr and command batches, file
content, writes, patches, loaded skill resources, skill discovery, scripts, web
searches, fetched content, roadmap tasks, and MCP resources/prompts. They accept
Pydantic objects, dictionaries restored from storage, and JSON-encoded results.
Binary payloads are described rather than printed. Unknown structures fall back
to labeled lines, not Python object representations.

A tool can declare a custom output formatter with
`@tools.tool(output=formatter)`, alongside the existing `describe=formatter`
summary hook. Output formatters receive normalized data and return `ToolOutput`.
For example:

```python
from typing import Any

from src.engine.tooling.outputs import ToolOutput
from src.engine.tooling.presentation import ToolSummary


def format_matches(data: Any) -> ToolOutput:
    paths = data['paths']
    return ToolOutput('\n'.join(paths), ToolSummary(f'{len(paths)} matches'))


@tools.tool(output=format_matches)
def find_matches() -> dict[str, list[str]]:
    return {'paths': ['src/app.py', 'tests/test_app.py']}
```

The registration applies to live results and replayed history in the same
process. A formatter failure uses the safe structured fallback and does not fail
the tool. Human output is bounded to 40,000 characters, with additional display
bounds in each client. Raw diagnostic previews remain separate from human output.
