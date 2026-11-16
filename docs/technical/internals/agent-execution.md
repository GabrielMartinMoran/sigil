# Subagents

A session is not one agent: it is a **tree**. The agent at the root is the one
you talk to — the one `/agent` and `ctrl+a` offer — and under it hang the
**subagents** it summoned. They are not selectable, they are not listed anywhere
except the tree, and they answer to the agent that started them.

A subagent has the same tools and permission rules as an agent, with its own
profile and context. It uses its configured model, or inherits its caller's
model and effort. What makes it a *sub*agent is who starts it and where its
answer goes.

## The role

A profile declares its role and permissions alongside its instructions, as in
this example.

```yaml
---
name: scout
description: Reads one thing end to end and reports what it found.
role: subagent
---
```

`role: agent` is the default, so every profile written before this existed keeps
being selectable. A profile with `role: subagent` is refused by `/agent` — the
agent at the root of a session is the one a person talks to — and refused by
`summon` if it says `role: agent`: an agent is not something another agent
starts.

## The `summon` permission

Starting a subagent is the `summon` tool, and it is ruled on like every other
tool: the **name of the subagent is the resource**.

```yaml
permissions:
  summon:
    allow: ["scout", "review-*"] # these two profiles...
    deny: ["review-security"] # ...except this one
```

- **No `summon` rule**: every subagent is available.
- **`summon: deny`**: the tool is not advertised at all; the model does not know
  it exists.
- **An allow list**: exhaustive. A name it does not cover is refused as a failed
  call the model can read, and the block in the prompt lists only the names that
  are left — an agent that may summon two subagents is told about two.

A subagent cannot summon itself, and the chain has a cap (`max_depth`, 3 by
default): an agent may summon ten subagents in a row, but a summon inside a
summon inside a summon inside a summon is a runaway.

When a node ends, whatever it had summoned and was still running is stopped with
it: a branch cannot hang from a parent that stopped, and its answer would have
nobody to reach. That is also why a killed node takes its descendants with it.

## Sync and async

The tool takes a `mode`:

| Mode             | What the caller sees                                                                                                                 |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `sync` (default) | Nothing until the subagent finishes. Then the tool result **is** the subagent's final message                                        |
| `async`          | An acknowledgement at once. The message arrives later, as a new user message from `[subagent Scout (scout #1a2b3c, async) finished]` |

`sync` is what you want when the next step depends on the answer. `async` is
what you want when the work is independent: the parent keeps going, and the
answer lands mid-turn if the turn is still open, or wakes the session in a new
turn if it is not.

**Ending the turn is how the agent waits.** The turn that dispatched the squad
finishes — the user reads "two researchers are running" — and the session goes
idle. When a report arrives, the app starts the turn it is owed, with the report
in front of it. It is debounced: three reports landing within a second are three
messages and **one** turn, because three turns would be three model requests to
read three paragraphs that belong together.

The flag that says "nobody has read this answer" travels with the session, so a
report that arrives while the app is closing is not lost: the next open finds it
waiting and starts the turn there.

An answer never interrupts a turn the user started: that turn drains the same
queue at the top of its prompt, which is why a `sync` call and a woken turn
share one path.

Stopping the turn (`Esc` twice) stops the async subagents it started: a
background node still spending tokens is not stopped.

## The id, and going back for more

`summon` returns the id of the execution it started, and that id is the handle
for everything else:

| Call                                           | What it does                                      |
| ---------------------------------------------- | ------------------------------------------------- |
| `summon(agent="scout", task="…")`              | A new node. Two calls without an id are two nodes |
| `summon(agent_id="1a2b3c", task="…")`          | Continues **that** node on top of its transcript  |
| `send_message(agent_id="1a2b3c", message="…")` | Steers it mid-run, or answers a notice            |
| `kill_subagent(agent_id="1a2b3c")`             | Stops it, and everything it summoned              |
| `list_agents()`                                | The ids, names, states and parents of the branch  |

The id is execution and the name is kind of agent: resuming `#1a2b3c` keeps the
profile it was started with, and summoning `scout` again starts from nothing. A
resume appends to the history instead of replacing it, so a subagent that read
half a tree keeps what it read.

## Running out of steps

A profile can declare its own budget:

```yaml
max_steps: 40
```

Running out is not an abort. The run loses its tools and gets **one more model
call**, with a message explaining that the budget is gone and what to write
instead: what it established, what it did not get to, and anything the caller
needs to continue. That last message is the subagent's report.

The parent can then pick it up with `summon(agent_id=…)` — the report is in the
node's transcript — instead of starting the investigation again. The tool result
says `out of steps` next to the node's id, which is the cue to do exactly that.

The turn of the session works the same way, and it is the same key: a profile's
`max_steps` is the budget of _that_ agent, whether it is the one at the root or
a subagent under it. `SIGIL_MAX_STEPS` (default `256`) is the fallback for every
profile that declares nothing, which is all of them until one asks. Reaching it
ends the turn with a summary rather than a cut-off, so a long turn is still
worth reading and the next message continues from there.

## Talking between agents

A subagent can be given a voice in its frontmatter:

```yaml
channel: bidirectional # it may send notices to its parent

channel: # ...and to the siblings it names
  mode: bidirectional
  parent: true
  siblings: ["reviewer", "scout"]
```

| Direction         | What it is                                                                 |
| ----------------- | -------------------------------------------------------------------------- |
| Parent → child    | An **instruction**: injected into the run like a message typed in the chat |
| Child → parent    | A **notice**: the parent reads it and decides whether to act               |
| Sibling → sibling | A **notice**, only in the direction the sender's channel names             |

A notice is information, not an order, and it says so in the message the
receiver reads. Nothing travels the other way by default: no `channel` means
unidirectional, the parent talks and the subagent answers once at the end.

**A message needs a live receiver, whoever sends it.** Nothing is queued for an
agent that is not running: a stopped subagent reads nothing, and a queue nobody
promised to drain is a queue that grows in the dark. The `send_message` call
fails with an error that says what to do instead, and the advice depends on who
is asking because the fix does:

| You     | Target                     | What the refusal tells you                                            |
| ------- | -------------------------- | --------------------------------------------------------------------- |
| Parent  | Your child, finished       | `summon(agent_id="…", task=<your message>)` — it keeps its transcript |
| Child   | Your parent, not in a turn | You cannot start it: put it in your final report                      |
| Sibling | A sibling, finished        | A sibling cannot resume it: ask your parent                           |

So answering a notice is a `summon` with the reply as the task, which is exactly
what the parent's own error says. One gesture, and the subagent reads the answer
on top of everything it already knew.

**A child never outlives its parent.** When a node ends, whatever was still
running under it is stopped, and a killed node takes its descendants with it.
There is no state where a branch hangs from an agent that is gone.

The ids a child needs to write to its neighbours are not something it is asked
to remember: a block of the prompt — **The agents around you** — lists its
parent, its children and the siblings its channel names, with the id and whether
each one is working right now. It is rebuilt on every request, so a sibling that
finished stops being reachable the moment it does.

## What belongs to one agent

Three things used to be session-wide and are now per node, because a tree makes
the difference visible:

| State                | Why it is per agent                                                                                                                                                 |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Loaded skills**    | A loaded skill injects its whole body into the instructions. Sharing them would put the Lead's design guidance into an engineer's prompt and spend its budget on it |
| **The roadmap**      | A plan belongs to whoever executes it: "what is the squad doing" is not "which unit am I in"                                                                        |
| **Context and cost** | The panel measures the transcript of whoever is on screen                                                                                                           |

The side panel follows the screen: opening a subagent shows its name at the top
of the column — the rest of the panel looks the same for a parent and its child
— and its plan, its skills, its context and its bill. The terminal window title
carries the node too, so a reader whose terminal is not focused still knows
which conversation is open.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
