"""The tools that make a tree of agents a place you can work in.

They share one vocabulary — the subagents an agent can start and
manage:

| Tool | What it does |
| --- | --- |
| `summon` | Starts a subagent, or resumes one by its id |
| `send_message` | Steers a child, or sends an informational notice |
| `ack_notifications` | Confirms receipt without removing notices |
| `kill_subagent` | Stops a subagent and everything under it |
| `list_agents` | Shows the ids, names and states of the branch |

`list_agents` is not a convenience: the ids are minted at run time, so the only
honest way for an agent to learn them (again, after a compaction, or after a
resume) is to ask. It is also what makes the tools usable without trusting the
prompt's table to still be accurate.

Every rule about *who* may do *what* lives in the summoner, not here, and the
permission is the tool's group: a profile that closes `summon` closes all tree tools,
because managing subagents is one capability and not a collection of independent permissions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from pydantic_ai import RunContext
from pydantic_ai.exceptions import ToolFailed

from src.engine.agents.tree.rendering import SUMMON_GROUP, SUMMON_TOOL
from src.engine.agents.tree.summoner import SigilDeps, SubagentSummoner, SummonError, SummonMode
from src.engine.tooling.catalog import ToolCatalog
from src.engine.tooling.compaction_policies import (
    ACK_NOTIFICATIONS_COMPACTOR,
    KILL_SUBAGENT_COMPACTOR,
    LIST_AGENTS_COMPACTOR,
    SEND_MESSAGE_COMPACTOR,
    SUMMON_COMPACTOR,
)
from src.engine.tooling.logging import ToolLogger
from src.engine.tooling.permission import ToolPermission
from src.engine.tooling.registrar import ToolRegistrar

if TYPE_CHECKING:
    from pydantic_ai import Agent

    from src.engine.agents.tree.tree import AgentTree

SUMMON_DESCRIPTION = (
    'Start a subagent: a separate agent with its own profile, model and context that does one '
    'task and reports back. It does not see this conversation, so write the task as a complete '
    'brief. `mode="sync"` blocks until it answers and returns that message; `mode="async"` '
    'returns immediately and delivers the message later as a new user message. Pass `agent_id` '
    'to continue a subagent that already ran: it keeps its transcript, so the task is a new '
    'instruction on top of what it did.'
)

SEND_DESCRIPTION = (
    'Send a message to a working agent of this session. To your direct child it is an instruction '
    'in its history (steering). To your direct parent or a sibling named by your channel it is an '
    'informational notice in its dynamic notification table, not a user instruction. Notices remain '
    'visible after ACK; ACK confirms receipt only. The receiver must be running.'
)

ACK_DESCRIPTION = (
    'Confirm receipt of notifications addressed to your execution, using the exact IDs in your '
    'notification table. ACK means OK, received, not read, agreed, acted on, or resolved. It changes '
    'only the state: content and sequence stay visible. Repeating an ACK is harmless. '
    'An invalid ID rejects the entire batch.'
)

KILL_DESCRIPTION = (
    'Stop a subagent and everything it summoned. Use it when a background subagent is no longer '
    'useful, or when one is clearly going the wrong way: its work is kept in the tree, so it can '
    'be resumed later with `summon(agent_id=…)`.'
)

LIST_DESCRIPTION = (
    'List the agents of this session with their ids, names, states and who summoned them. The ids '
    'are what `summon(agent_id=…)`, `send_message` and `kill_subagent` take.'
)

_SUMMON = ToolPermission(groups=SUMMON_GROUP)
"""No resource: the permission is the tool, not the name inside the call.

`summon` narrows by subagent *name* because that is what a profile declares. The
other tools address execution or notification IDs minted at runtime, so they
are ruled on whole-tool: `summon: deny` closes the group, and a narrowed allow list
narrows only what may be summoned.
"""


def summon_permission(summoner: SubagentSummoner) -> ToolPermission:
    """Check the actual target profile, including when resuming by execution ID."""

    def resolve(args: Mapping[str, Any]) -> tuple[str, ...]:
        agent_id = args.get('agent_id')
        if agent_id:
            try:
                return (summoner.resolve(str(agent_id)).name,)
            except SummonError as error:
                raise ToolFailed(str(error)) from error
        name = str(args.get('agent') or '').strip()
        return (name,) if name else ()

    return ToolPermission(groups=SUMMON_GROUP, resource='agent', resolve_resource=resolve)


def register_tree_tools(
    agent: Agent[Any, Any],
    summoner: SubagentSummoner,
    *,
    logger: ToolLogger | None = None,
    catalog: ToolCatalog | None = None,
) -> None:
    """Registers the tools of the tree on ``agent``.

    ``catalog`` is the one the agent owns: without it these tools declare their level of
    detail into a catalogue nobody reads, and the context view falls back to the generic
    size policy for them.
    """
    registrar = ToolRegistrar(agent, logger, catalog)

    async def summon(
        ctx: RunContext[SigilDeps],
        task: str,
        agent: str = '',
        mode: SummonMode = 'sync',
        agent_id: str = '',
    ) -> str:
        """Summon a subagent, or continue one that already ran.

        Args:
            task: What it has to do, or the new instruction for a resumed subagent.
                It cannot see this conversation: include every path, name and detail
                it needs, and say what to report back.
            agent: Name of the subagent profile, exactly as listed in your
                instructions. Ignored when `agent_id` is given.
            mode: `sync` to wait for the answer, `async` to keep working and receive
                it later as a new user message.
            agent_id: Id of a subagent that already ran (or that ran out of steps),
                to continue it on top of its own transcript instead of starting over.
        """
        # `getattr` and not `ctx.deps.node_id`: a run started without deps (a test,
        # an embedding) must summon from the root instead of failing.
        return await summoner.summon(
            parent_id=getattr(ctx.deps, 'node_id', None),
            name=agent,
            task=task,
            mode=mode,
            agent_id=agent_id or None,
        )

    async def send_message(ctx: RunContext[SigilDeps], agent_id: str, message: str) -> str:
        """Send a message to another agent of this session.

        Args:
            agent_id: Id of the agent to message, as returned by `summon` or listed
                by `list_agents`.
            message: What to tell it. To a child this is an instruction; to your
                parent or a sibling it is a notice, which they may or may not act on.
        """
        return summoner.send_message(
            sender_id=getattr(ctx.deps, 'node_id', None),
            target_id=agent_id,
            text=message,
        )

    async def ack_notifications(ctx: RunContext[SigilDeps], notification_ids: list[str]) -> str:
        """Confirm receipt of notices without removing them from your table.

        Args:
            notification_ids: Exact notification IDs from your table, not agent IDs.
        """
        return summoner.ack_notifications(
            receiver_id=getattr(ctx.deps, 'node_id', None), notification_ids=notification_ids
        )

    def kill_subagent(ctx: RunContext[SigilDeps], agent_id: str) -> str:
        """Stop a subagent, and everything it summoned.

        Args:
            agent_id: Id of the agent to stop, as returned by `summon` or listed by
                `list_agents`.
        """
        return summoner.kill(getattr(ctx.deps, 'node_id', None), agent_id)

    def list_agents(ctx: RunContext[SigilDeps]) -> str:
        """List the agents of this session: ids, names, states and who summoned them."""
        node_id = getattr(ctx.deps, 'node_id', None)
        return render_tree_table(summoner.tree(), node_id)

    registrar.register(
        summon,
        use_context=True,
        name=SUMMON_TOOL,
        description=SUMMON_DESCRIPTION,
        permission=summon_permission(summoner),
        compaction=SUMMON_COMPACTOR,
    )
    registrar.register(
        send_message,
        use_context=True,
        name='send_message',
        description=SEND_DESCRIPTION,
        permission=_SUMMON,
        compaction=SEND_MESSAGE_COMPACTOR,
    )
    registrar.register(
        ack_notifications,
        use_context=True,
        name='ack_notifications',
        description=ACK_DESCRIPTION,
        permission=_SUMMON,
        compaction=ACK_NOTIFICATIONS_COMPACTOR,
    )
    registrar.register(
        kill_subagent,
        use_context=True,
        name='kill_subagent',
        description=KILL_DESCRIPTION,
        permission=_SUMMON,
        compaction=KILL_SUBAGENT_COMPACTOR,
    )
    registrar.register(
        list_agents,
        use_context=True,
        name='list_agents',
        description=LIST_DESCRIPTION,
        permission=_SUMMON,
        compaction=LIST_AGENTS_COMPACTOR,
    )


def render_tree_table(tree: AgentTree | None, viewer_id: str | None) -> str:
    """The tree as a compact table, marking who is asking.

    It is what the model reads when it calls `list_agents`: one line per node, the
    id first because that is what every other tool takes.
    """
    if tree is None:
        return 'There is no agent tree in this session.'
    nodes = tree.nodes()
    if len(nodes) <= 1:
        return 'This session has no subagents yet.'
    lines = ['id | agent | state | mode | who summoned it | task']
    for node in nodes:
        parent = tree.parent(node.id)
        marker = ' (you)' if node.id == viewer_id else ''
        task = ' '.join(node.task.split())[:70]
        lines.append(
            f'{node.short_id} | {node.name}{marker} | {node.status} | {node.mode} | '
            f'{parent.short_id if parent is not None else "-"} | {task}'
        )
    return '\n'.join(lines)


__all__ = [
    'ACK_DESCRIPTION',
    'KILL_DESCRIPTION',
    'LIST_DESCRIPTION',
    'SEND_DESCRIPTION',
    'SUMMON_DESCRIPTION',
    'register_tree_tools',
    'render_tree_table',
]
