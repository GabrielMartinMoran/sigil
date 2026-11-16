# Follow delegated work

Your session agent can delegate an investigation to a subagent with its own
profile, context, and tools. You keep talking to the main agent; the subagent
reports to the agent that started it.

## Open the agent tree

Press **Ctrl+T** to see the session's executions, states, and elapsed time.
Select a row with **Enter**, or click a subagent's spawn block in the chat. The
tree opens even when no subagents have been created yet.

Active branches appear before finished ones and keep their descendants together.
Use `/` to filter by name, ID, state, or task. **Esc** in the filter clears it;
**Esc** in a subagent view returns to its parent.

## Read a subagent's work

Opening an execution shows its transcript and its own sidebar: loaded skills,
plan, context, and cost. You cannot type into that transcript. Return to the
main conversation to give instructions to your session agent.

Saved sessions retain recorded subagents, their transcripts, and outcomes.
Loading a session doesn't mean a previously finished or interrupted execution is
silently running again.

## Background or stop a branch

Use these actions in the tree deliberately:

| Key                   | Action                                                              |
| --------------------- | ------------------------------------------------------------------- |
| **b**                 | Stop waiting on a blocking subagent while letting its work continue |
| **k**                 | Stop the selected execution and its descendants                     |
| **Enter** on the root | Return to the main conversation                                     |

Background work can still spend provider tokens and affect external systems. A
completed asynchronous report returns to the main agent; it can prompt a
follow-up turn when the session is idle. Stopping work doesn't undo completed
effects. Cancelling the parent turn also stops asynchronous work started by it.

To add your own agent or subagent profile, use
[Agent profiles](../customization/agents.md). For sync/async execution,
messaging, step budgets, and lifecycle contracts, read
[Agent execution](../../technical/internals/agent-execution.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
