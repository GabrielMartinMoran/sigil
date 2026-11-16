# Working while the agent runs

The agent does not take the keyboard hostage. While a turn runs you can keep
typing: a plain message is **queued** for that turn and injected into it, and
only the commands that would pull the session out from under the running agent
are refused.

The keyboard has exactly one owner, and reading the interface does not cost it:
clicking the transcript, the side panel or the debug view scrolls and selects
but leaves the keys in the composer. The conversation is read from the composer
with the keyboard:

| Key                 | What it does                          |
| ------------------- | ------------------------------------- |
| Mouse wheel         | Scrolls whatever is under the pointer |
| `pgup` / `pgdn`     | Moves the conversation one page       |
| `ctrl+↑` / `ctrl+↓` | Moves it a few lines                  |

The same `ctrl+↑` / `ctrl+↓` pair reads the conversation while a question panel
is open, so the keys do not change meaning between the two surfaces.

## Sending a message mid-turn

Type it and press `Enter`, exactly as when the agent is idle. The chat shows it
with a `queued for the running turn` mark until the run reads it.

Delivery happens at the earliest point where the conversation is still valid:

- the agent finishes the tool batch it is waiting on, and **the message lands
  right after the tool results**, before the model decides what to do next;
- or the agent was about to end the turn, and the message gives it one more step
  instead of becoming a separate turn.

Either way the model reads it as a new user instruction, so a correction arrives
while the work is in progress. The chat, while the tool is still running and
right after the message is read:

```text
› refactor the parser                                    │  › refactor the parser
› use the new tokenizer  · queued for the running turn    │  › use the new tokenizer
                                                         │  ↳ 1  """Parser entry point."""
```

On the left the message is on screen immediately, marked as waiting; on the
right the mark is gone because the run has already injected it. The model reads
it after the tool result it was waiting for, before deciding what to do next.

The chat is a live log, so what it shows is the order things arrived: a message
typed before a tool finished appears above that tool's result. What the model
reads is the order in the history, which is the one that matters and the one
`/load` renders back.

The queue is not a second conversation: the message goes into the same history,
in order, and the session on disk records it in the position the model saw it.

A message that misses the last seam -- the turn ended before it could be
delivered -- goes back to the prompt, and the chat says so. It is never dropped
silently.

## What you can still do mid-turn

Read-only and display controls remain separate from commands that reconfigure an
active run.

| Command                                                                                              | Mid-turn                                                               |
| ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- |
| `/context`, `/debug`, `/sidebar`, `/help`                                                            | Available: they only read state                                        |
| `/agent`, `/provider`, `/model`, `/default-models`, `/effort`, `/external-files`, `/mcp`, `/compact` | Refused: they reconfigure the active execution                         |
| `/new`, `/load` in the service-backed terminal                                                       | Select another session without cancelling work in the previous session |
| `/quit` in the service-backed terminal                                                               | Detach the client; ongoing service work is not implicitly aborted      |
| `/rollback`                                                                                          | Requires the selected session to be idle                               |

A refused command does nothing beyond saying so: the chat gets one yellow line
naming it, and the command goes back into the prompt — the turn, the session,
the history and the chat are left exactly as they were. It is refused whether
you type it or pick it from the `/` menu with `Enter`.

`/model` and `/effort` are refused up front, before the catalogue is fetched and
the picker opens. The service would also refuse the change, but only after a
network round-trip and a modal you would have to cancel.

## Stopping a turn

`Esc` stops the agent, and it asks first: stopping throws away what the model
was writing, so it is not a single keystroke's decision.

1. Press `Esc`. The status bar under the prompt says
   `Press Esc again to stop the agent.`
2. Press `Esc` again within three seconds. The turn stops.

Anything else cancels the request: any other key, or the three seconds running
out. The bar goes back to empty and the agent keeps working.

Stopping is not a failure and not an error:

- The run is cancelled where it is, and the shell commands it spawned are killed
  by process group, so a build or a `sleep` does not outlive the turn.
- The chat says `Turn cancelled.`
- What the run completed stays in the session. The tool calls that were cut off
  mid-flight are closed out, so the next message starts from a history a
  provider accepts.

`Esc` inside an `ask_user` panel is different: there it closes the panel and
ends the turn in one press, because the panel is already asking you something
and a confirmation on top would be a second question to answer.

## Turn limits

A turn ends early for two reasons.

`SIGIL_MAX_STEPS` (default `256`) is the budget. One _model call_ counts as one
step. When it runs out the turn is not aborted: the tools are taken away and the
model gets one last call, with a message explaining that the budget is gone, to
write what it established and what is left. The chat says so, and what was
completed stays in the session — so a long turn ends with a report instead of
stopping mid-sentence.

The other reason is you, with `Esc` twice (see
[Stopping a turn](#stopping-a-turn)) or with a single `Esc` inside an `ask_user`
panel. The turn stops on purpose, so no error is reported; the chat says
`Turn cancelled.`, and the history keeps what already happened.

Either way the session stays usable and the next message starts a new turn
instead of failing on a call that never got its result. Sigil closes the gap
itself when the turn ends, so a call cut off mid-flight does not survive in the
saved history.

pydantic-ai brings its own cap of 50 requests per run. Sigil disables it
(`request_limit=None`) so the only limit is the step one: otherwise a long turn
died with a `UsageLimitExceeded` nobody configured, before reaching the real
limit.

## A crashed turn does not break the session

A turn is saved after every step, which is what lets a long turn survive the
process dying halfway: what the run completed is already on disk. The flip side
is that the last save can land in the middle of a tool batch, with a model
response that asked for tools and no results behind it.

pydantic-ai refuses a new prompt on top of that, and it refuses it every time:
one crash that lands in that window used to break the session for every message
after it, with
`Cannot provide a new user prompt when the message history contains unprocessed tool calls`
in the chat.

Sigil checks the history at both ends of a turn:

1. At the start, before anything reaches the agent. This is the case a crashed
   process leaves behind: the app is opened again on a broken session.
2. At the end, on every path. A cancelled turn, a turn that died on an error
   while a tool ran, and a step limit all leave a tail a provider would reject,
   and the save that follows would write it to disk.

Either way the damage is closed out. When the repair happens at the start of a
turn the chat says `Recovered the session: …`, because that is damage a previous
process left behind, and you need to know the session you opened was not what
you saved. When it happens at the end of a turn the chat has already reported
what ended it (`Turn cancelled.`, the error, the step limit), so the repair
stays quiet.

The synthesized results are marked with `pydantic_ai_synthesized_tool_return`,
so nothing pretends a tool ran when it didn't. A healthy history is left exactly
as it was — no copy, no reordering — so the model's prompt cache prefix is never
churned.

---

[Documentation index](../../README.md) · [Technical documentation](../README.md)
