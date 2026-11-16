"""Summoning a subagent: the tree, the two modes and the way back.

A subagent runs the same tools with its own profile prompt and a configured or
inherited model. What makes it a *sub*agent is who started it and where its
message goes. This module starts it and carries messages in both directions.

**Sync and async are two ways of waiting, not two kinds of agent.**

- ``sync``: the tool does not return until the subagent finishes, and the tool
  result *is* the subagent's message. The parent reads it as a result, which is
  what it wants when the next step depends on it.
- ``async``: the tool returns as soon as the node exists, and the message is
  delivered later as a **user message** to whoever summoned it. That is the only
  honest shape for a callback inside an agent run: a tool result cannot arrive
  after its turn closed, but a message can be enqueued, and the parent model reads
  it as a new instruction with the content in front of it.

Both modes are the *same* run, started as a task and tracked; the mode only decides
whether the caller waits. That is what makes the other two verbs possible:

- ``detach``: a sync caller stops waiting, the node keeps working and its answer
  arrives later as an async one. This is the "send it to the background" of the
  UI, and it costs one flag because the run was never in the caller's stack.
- ``kill``: the task is cancelled and the node is marked ``cancelled``.

**Resuming is not respawning.** A node keeps its transcript, so a second call with
its id continues the same conversation instead of starting from nothing: the new
prompt is appended to what the node already did. A call *without* an id always
starts a new node, even for the same profile — the id is the execution, the name is
the kind of agent.

**A child cannot outlive its parent.** When a node ends, whatever was still running
under it is stopped: its answer has nobody to reach, and keeping it alive would
leave a branch hanging from a parent that no longer exists.

**Messages travel by the channel of the sender's profile.** A parent steers its
child (an instruction); a child, if it declared a bidirectional channel, sends
*notices* to its parent and to the siblings it named. A notice is information, and
the prompt says so: the receiver decides whether it deserves an answer.
"""

from __future__ import annotations

from src.core.agents.tree.agent_run_model import AgentRunModel
from src.core.agents.tree.sigil_deps import SigilDeps
from src.core.agents.tree.subagent_summoner import _ACTIVE_AGENT_MODEL

"""How the caller waits: blocked on the result, or free until it arrives."""

"""The states whose text is a report a parent may be handed.

A cancelled node is not one. It keeps whatever it had said and keeps
``delivered`` false if the cancellation came before anyone read it, and handing
that to a turn would re-run work somebody stopped on purpose.
"""

"""How deep summoning may nest.

Depth 1 is the normal case: the agent summons a subagent. Depth 2 means a subagent
summoned another, which is allowed — it is the same tool — but it is also the
shape that runs away when nobody is watching, so the chain is capped. The cap is
about the chain, not the branch: an agent may summon ten subagents in a row.
"""


def current_agent_model() -> AgentRunModel | None:
    return _ACTIVE_AGENT_MODEL.get()


"""A node was born or changed state. The UI refreshes the tree."""

"""The run of the agent at the root, while it is alive, or ``None``.

It is a callable and not a value: the app owns the turn, the turn owns the run, and
both change between the moment the summoner is built and the moment a message
arrives. ``None`` — or a run that already ended — means the message has to wait for
the next real turn.
"""

"""A message was handed to a live run. The UI says so in the chat."""

"""An answer is waiting and no turn is running to read it.

The app decides what that means: it may start a turn on its own, or leave the
answer for the next one. The engine does not start turns; it only says that there
is something to read, because a turn is a user-facing act and the app owns it.
"""

"""Builds the agent object of one profile. The tree node is not its concern."""


__all__ = ['SigilDeps']
