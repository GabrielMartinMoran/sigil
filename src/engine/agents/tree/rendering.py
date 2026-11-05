"""The blocks that describe the tree: who is there, and what is waiting.

Three blocks, each answering one question, each rebuilt per request:

- **Who you can summon.** A catalogue of profiles. Rebuilt because it changes when
  a file is dropped in ``.sigil/agents``, and because the permissions narrow it: an
  agent whose ``summon`` rule names two profiles is told about two. Offering a name
  the capability is about to refuse would teach the model to make calls that fail.
- **Who is next to you.** The peers of one *node*: its parent, its children and the
  siblings its channel names, with the id and whether each one is running. This is
  the block that makes the tree usable, because the ids are minted at run time and
  a model cannot be asked to remember them across a context trim.
- **Who is next to you.** The peers of one *node*... see below.

The middle one is per *node* while the first is per *profile*, so the two are
rendered from different places: the catalogue goes with the profile, and the peers
block travels with the run (it reads ``ctx.deps.node_id``). Rendering the peers per
profile would tell a finished subagent about a sibling that finished an hour ago.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from src.engine.tooling.permission import ResourceKind, ToolGroup

if TYPE_CHECKING:
    from src.engine.agents.profile.models.agent_profile import AgentProfile
    from src.engine.agents.profile.registry import AgentRegistry
    from src.engine.agents.tree.models.agent_node import AgentNode
    from src.engine.agents.tree.tree import AgentTree

SUMMON_TOOL = 'summon'
"""Name of the tool, which is also the permission scope that narrows it."""

SUMMON_GROUP = (ToolGroup.SUMMON,)
"""The group the tree's tools declare, for the rules that target the group."""


def summonable(registry: AgentRegistry, profile: AgentProfile | None) -> list[AgentProfile]:
    """The subagents ``profile`` may start, in registry order.

    A subagent is not offered itself. It *could* — the permissions do not forbid it
    — but a profile that summons its own clone is a loop waiting for the depth cap,
    and the list is read as "who else can help".
    """
    if profile is None:
        return []
    if not profile.permissions.advertises(SUMMON_TOOL, SUMMON_GROUP):
        return []
    return [
        candidate
        for candidate in registry.subagents()
        if candidate.name != profile.name
        and profile.permissions.permits(SUMMON_TOOL, SUMMON_GROUP, (candidate.name,), ResourceKind.TEXT)
    ]


def render_subagents(registry: AgentRegistry, profile: AgentProfile | None) -> str:
    """The catalogue of subagents, or ``''`` when there is nobody to list."""
    candidates = summonable(registry, profile)
    if not candidates:
        return ''

    lines = [
        '## Subagents you can summon',
        '',
        'Each one runs in a session of its own: it does not see this conversation, so',
        'the task you give it has to carry everything it needs. Call `summon` with the',
        'name, the task, and `mode="sync"` to wait for the answer or `mode="async"` to',
        'keep working and receive it later as a message. `summon` returns an id: pass it',
        'as `agent_id` to continue that same subagent — it keeps its transcript — and to',
        '`send_message` or `kill_subagent` to talk to it or stop it.',
        '',
    ]
    for candidate in candidates:
        lines.append(f'- **`{candidate.name}`** — {" ".join(candidate.description.split())}')
    return '\n'.join(lines).rstrip() + '\n'


def render_peers(
    tree: AgentTree | None,
    node_id: str | None,
    *,
    profile: AgentProfile | None = None,
    is_live: Callable[[AgentNode], bool] | None = None,
) -> str:
    """The nodes next to one node, with the id each one answers to.

    ``profile`` is the profile of the *viewer*: it decides, together with the tree,
    whether a relationship is a channel that exists. A sibling appears only when the
    viewer's channel names it, because listing one it cannot write to would be an
    invitation to a refusal.

    ``is_live`` answers whether a node has a run *right now*. It matters for the
    root: its status is ``running`` for the whole session, but an agent the user is
    not talking to cannot read a message, and saying otherwise would have a subagent
    send a notice into a queue while believing it was read.
    """
    if tree is None:
        return ''
    node = tree.root if node_id is None else tree.node(node_id)
    if node is None or node.is_root:
        return ''
    channel = profile.channel if profile is not None else node.channel
    groups: list[tuple[str, str, list[AgentNode]]] = []

    parent = tree.parent(node.id)
    if parent is not None:
        relation = 'notices, not orders' if channel.may_message_parent() else 'it can message you'
        groups.append(('Your parent', relation, [parent]))
    children = tree.children(node.id)
    if children:
        groups.append(('Agents you summoned', 'instructions', children))
    siblings = [
        sibling
        for sibling in tree.siblings(node.id)
        if channel.may_message_sibling(sibling.name) and not sibling.is_root
    ]
    if siblings:
        groups.append(('Siblings you may message', 'notices, not orders', siblings))

    if not groups:
        return ''
    lines = [
        '## The agents around you',
        '',
        'The id is what `send_message`, `kill_subagent` and `summon(agent_id=…)` take.',
        'You can only message an agent that is working: the others are kept so you know',
        'they exist, and your parent can resume them.',
        '',
    ]
    for title, hint, members in groups:
        lines.append(f'### {title} — {hint}')
        for member in members:
            lines.append(f'- `{member.short_id}` · **{member.name}** · {_state(member, is_live)}')
        lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def _state(node: AgentNode, is_live: Callable[[AgentNode], bool] | None) -> str:
    """One word for what a neighbour is doing, from the point of view of a writer."""
    if not node.status.finished:
        if is_live is not None:
            return 'working' if is_live(node) else 'idle (not in a turn)'
        return 'working'
    if node.budget_exhausted:
        return 'done (out of steps)'
    return str(node.status)


__all__ = [
    'SUMMON_GROUP',
    'SUMMON_TOOL',
    'render_peers',
    'render_subagents',
    'summonable',
]
