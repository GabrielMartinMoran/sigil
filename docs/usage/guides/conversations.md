# Steer and stop a conversation

A turn starts when you send a message and ends when the agent finishes. You can
keep typing while it works; you don't need to stop it to add a correction.

## Send a correction while the agent works

Type the correction and press **Enter**. It appears with a queued marker until
the running turn reads it, normally after its current batch of tools finishes.
It then becomes part of the same conversation, not a competing second turn.

If the turn ends before the message can be delivered, Sigil returns it to the
prompt and tells you. Check that notice before sending it again.

Commands that reconfigure the active model, agent, or tools are refused while a
turn is active. In the default service-backed terminal you can select another
session with `/new` or `/load` without cancelling the previous session's work.
Closing the terminal also detaches rather than aborts that work. See the
[command reference](../reference/commands.md).

## Stop a turn

Use the two-press confirmation when no question panel is open:

1. Press **Esc**. The status bar asks you to press it again.
2. Press **Esc** again within three seconds to stop the agent.

Another key or an expired confirmation leaves the turn running. Completed work
stays in the session; cancellation doesn't undo files or external actions. To
return to an earlier state, inspect [rollback](rollback.md) separately.

When a question panel is open, **one Esc** cancels the question and ends the
turn. It doesn't choose an answer on your behalf.

## When the step budget runs out

The default budget is 256 model calls per turn unless a profile sets its own
`max_steps`. On exhaustion, the model gets a final tool-free call to summarize
what it established and what remains. You can continue with another message.

This budget isn't a spending cap. Model calls vary in price, and tools or
maintenance requests can have their own cost. Use
[Context and cost](context-and-cost.md) and the
[environment reference](../reference/environment.md) for controls.

## Recover after a failed turn

A cancelled or failed turn keeps completed history. Sigil repairs unfinished
tool exchanges before a later request so a missing result doesn't permanently
block the conversation. Repair isn't evidence that the interrupted tool
completed.

If the backend process died, its live work is lost. Follow
[service recovery](../troubleshooting/service-recovery.md) rather than assuming
a reconnection resumed every process or external effect.

For execution and history-repair details, read
[Turn lifecycle](../../technical/internals/turn-lifecycle.md).

---

[Documentation index](../../README.md) · [User documentation](../README.md)
