"""The tree of agents of one session.

One root — the agent the user talks to — and every subagent summoned under it.
The tree is the owner of the nodes: it mints their ids, keeps them in one place,
and answers the three questions the rest of the system asks — who is the root,
who is under whom, and what is still running.

Nesting is allowed. A subagent may summon another subagent, so a node's parent is
whatever node its ``summon`` came from; the tree does not assume depth 1. What it
does assume is a single root: a session is one conversation, and the agent the
user is talking to is the one at the top of it.

Ordering is depth-first and, among siblings, by start time. That is the order the
tree view paints and the order a reader expects: a finished branch is read before
the next one starts, and the ids never decide what comes first.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.tree.models.agent_node import ROOT_TASK, AgentNode
from src.engine.agents.tree.models.node_status import NodeStatus


class AgentTree:
    """Every agent of the session, rooted at the one the user talks to."""

    def __init__(self, nodes: Sequence[AgentNode] = ()) -> None:
        self._nodes: dict[str, AgentNode] = {}
        self._root_id = ''
        for node in nodes:
            self._adopt(node)

    def _adopt(self, node: AgentNode) -> None:
        """Registers a node read from disk, keeping the first root it finds."""
        if node.id in self._nodes:
            return
        self._nodes[node.id] = node
        if node.parent_id is None and not self._root_id:
            self._root_id = node.id

    # --- the root ----------------------------------------------------------

    @property
    def root_id(self) -> str:
        return self._root_id

    @property
    def root(self) -> AgentNode | None:
        return self._nodes.get(self._root_id)

    def ensure_root(self, name: str, node_id: str | None = None) -> AgentNode:
        """The root node, created empty on the first call.

        The name follows the session's agent: `/agent` changes who answers, and a
        tree that kept saying ``Wizard`` over a session run by another profile
        would be lying about the branch everything hangs from.

        ``node_id`` is the id the session derived for its root execution. The
        database and the tree have to agree on it before either has written a row,
        or a resumed session would mint a second root for the same conversation.
        """
        root = self.root
        if root is None:
            root = AgentNode(
                id=node_id or uuid.uuid4().hex,
                name=name,
                role=AgentRole.AGENT,
                parent_id=None,
                depth=0,
                task=ROOT_TASK,
                status=NodeStatus.RUNNING,
            )
            self._nodes[root.id] = root
            self._root_id = root.id
            return root
        if name and root.name != name:
            root.name = name
        return root

    # --- queries -----------------------------------------------------------

    def node(self, node_id: str | None) -> AgentNode | None:
        return self._nodes.get(node_id) if node_id else None

    def parent(self, node_id: str) -> AgentNode | None:
        node = self.node(node_id)
        return None if node is None or node.parent_id is None else self.node(node.parent_id)

    def children(self, node_id: str | None) -> list[AgentNode]:
        """Direct children of a node, oldest first. ``None`` means the root."""
        target = self.root_id if node_id is None else node_id
        found = [node for node in self._nodes.values() if node.parent_id == target]
        return sorted(found, key=lambda node: node.started_at)

    def nodes(self) -> list[AgentNode]:
        """Every node, depth-first from the root: the order the tree view reads."""
        ordered: list[AgentNode] = []
        seen: set[str] = set()

        def walk(node: AgentNode) -> None:
            if node.id in seen:
                # A cycle is impossible through `add`, and a hand-edited file can
                # still contain one. Walking it would be an infinite tree.
                return
            seen.add(node.id)
            ordered.append(node)
            for child in self.children(node.id):
                walk(child)

        root = self.root
        if root is not None:
            walk(root)
        # A node whose parent was dropped on load is not reachable from the root.
        # It is still a run that happened, so it is shown at the end rather than
        # thrown away.
        orphans = [node for node in self._nodes.values() if node.id not in seen]
        return [*ordered, *sorted(orphans, key=lambda node: node.started_at)]

    def path(self, node_id: str) -> list[AgentNode]:
        """Root first, ending at ``node_id``. Empty when the node is unknown."""
        chain: list[AgentNode] = []
        seen: set[str] = set()
        node = self.node(node_id)
        while node is not None and node.id not in seen:
            seen.add(node.id)
            chain.append(node)
            node = self.parent(node.id)
        chain.reverse()
        return chain

    def running(self) -> list[AgentNode]:
        """The nodes still working, in tree order."""
        return [node for node in self.nodes() if not node.status.finished]

    def ancestors(self, node_id: str) -> list[AgentNode]:
        """Parent first, root last. Empty for the root and for unknown ids."""
        chain: list[AgentNode] = []
        seen: set[str] = set()
        node = self.parent(node_id)
        while node is not None and node.id not in seen:
            seen.add(node.id)
            chain.append(node)
            node = self.parent(node.id)
        return chain

    def descendants(self, node_id: str) -> list[AgentNode]:
        """Everything under ``node_id``, depth first. The node itself is not one."""
        found: list[AgentNode] = []

        def walk(current: str) -> None:
            for child in self.children(current):
                found.append(child)
                walk(child.id)

        walk(node_id)
        return found

    def siblings(self, node_id: str) -> list[AgentNode]:
        """The other nodes that hang from the same parent, oldest first."""
        node = self.node(node_id)
        if node is None:
            return []
        return [candidate for candidate in self.children(node.parent_id) if candidate.id != node_id]

    def can_reach(self, actor_id: str, target_id: str) -> bool:
        """Whether ``actor_id`` may speak for ``target_id``: it is above it in the tree.

        Every rule about who may resume, message or kill whom is this question: a
        node has authority over the branch it started, never over its own parent or
        its siblings.
        """
        return any(ancestor.id == actor_id for ancestor in self.ancestors(target_id))

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, node_id: object) -> bool:
        return isinstance(node_id, str) and node_id in self._nodes

    # --- growing -----------------------------------------------------------

    def add(
        self,
        *,
        parent_id: str | None,
        name: str,
        role: AgentRole,
        task: str = '',
        mode: str = 'sync',
        model: str = '',
    ) -> AgentNode:
        """Adds a subagent under ``parent_id`` and returns it.

        The depth is derived, not passed: it is a property of where the node hangs,
        and a caller that counted it itself would be able to get it wrong.
        """
        parent = self.node(parent_id) or self.root
        depth = 0 if parent is None else parent.depth + 1
        node = AgentNode(
            id=uuid.uuid4().hex,
            name=name,
            role=role,
            parent_id=None if parent is None else parent.id,
            depth=depth,
            task=task,
            mode=mode,
            model=model,
        )
        self._nodes[node.id] = node
        return node

    # --- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {'nodes': [node.to_dict() for node in self.nodes()]}

    @classmethod
    def from_dict(cls, data: Any) -> AgentTree | None:
        """Rebuilds a tree, or ``None`` when there is nothing usable in it."""
        if not isinstance(data, Mapping):
            return None
        raw = data.get('nodes')
        if not isinstance(raw, list):
            return None
        nodes: list[AgentNode] = []
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            node = AgentNode.from_dict(item)
            if node is not None:
                nodes.append(node)
        if not nodes:
            return None
        # The root has to come first for `_adopt` to pin it, and it is the one the
        # serialization wrote first anyway.
        nodes.sort(key=lambda node: (node.parent_id is not None, node.started_at))
        return cls(nodes)


__all__ = ['AgentTree']
