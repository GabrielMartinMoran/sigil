"""One roadmap per agent of the session.

A roadmap is a plan, and a plan belongs to whoever is executing it. The Lead's plan
is not the engineer's: they answer different questions — "what is the squad doing"
against "which unit am I in" — and one list under both of them would be a list that
lies to one of the two.

So the store is per node. The root keeps the session's own roadmap (the file it
always had, named after the session), and a subagent gets a file of its own named
after its execution. The suffix is the node id, not the profile name: two concurrent
researchers are two different plans even when they are the same kind of agent.

The registry is per node for the same reason the loaded skills are: a plan belongs
to the execution that is executing it. Unlike the skills, this one *is* stored — a
plan cannot be derived from a transcript, because a task can be re-opened without a
new tool call.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from src.engine.roadmap.persistence import directory_for
from src.engine.roadmap.store import RoadmapStore

if TYPE_CHECKING:
    from src.engine.agents.tree.models.agent_node import AgentNode


class RoadmapStores:
    """The roadmap of each agent, keyed by node (``None`` for the root)."""

    def __init__(self, directory: str | Path | None = None, session_id: str | None = None) -> None:
        self._directory = Path(directory) if directory is not None else None
        self._session_id = session_id
        self._by_node: dict[str | None, RoadmapStore] = {}
        self._listeners: list[Callable[[], None]] = []

    # --- what the rest of the app asks for ---------------------------------

    def for_node(self, node_id: str | None) -> RoadmapStore:
        """The roadmap of one node, created (and loaded) on first use."""
        store = self._by_node.get(node_id)
        if store is None:
            store = RoadmapStore(directory=self._directory, session_id=self._file_id(node_id))
            store.add_listener(self._notify)
            self._by_node[node_id] = store
        return store

    @property
    def root(self) -> RoadmapStore:
        return self.for_node(None)

    def _file_id(self, node_id: str | None) -> str | None:
        """The name of the file: the session for the root, session and node for a child."""
        if not self._session_id:
            return None
        if node_id is None:
            return self._session_id
        return f'{self._session_id}--{node_id[:8]}'

    # --- lifecycle ---------------------------------------------------------

    def bind_session(self, session_id: str | None, directory: str | Path | None = None) -> None:
        """Points every roadmap at another session and drops the stale ones.

        A session swap invalidates the node ids, so the stores for the ones that
        were open are discarded: keeping them would leave the next session reading
        a roadmap that belongs to a conversation the user is no longer in.

        Re-binding the same session is a no-op. It happens on every mount and on
        every view change, and recreating the stores there would throw away live
        listeners — and the items of a plan that is still open.
        """
        target = None if directory is None else Path(directory)
        if session_id == self._session_id and (target is None or target == self._directory):
            return
        self._session_id = session_id
        if target is not None:
            self._directory = target
        for store in self._by_node.values():
            store.remove_listener(self._notify)
        self._by_node.clear()
        self._notify()

    def rebind_root(self, root: str | Path) -> None:
        """Moves every roadmap to another workspace."""
        self._directory = directory_for(root)
        for store in self._by_node.values():
            store.rebind_root(root)
        self._notify()

    def discard(self, node_id: str) -> None:
        """Forgets a node's roadmap. Called when its execution is gone for good."""
        store = self._by_node.pop(node_id, None)
        if store is not None:
            store.remove_listener(self._notify)
            path = store.path
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    # A file that cannot be removed is a leftover, not a failure:
                    # the node is gone either way.
                    pass
            self._notify()

    # --- notifications -----------------------------------------------------

    def add_listener(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    # --- what the tools read ----------------------------------------------

    def node_of(self, tree: AgentNode | None) -> AgentNode | None:
        """Kept for symmetry with the summoner: a node, or nothing."""
        return tree


def path_of(stores: RoadmapStores, node_id: str | None) -> Path | None:
    """The file backing a node's roadmap. For diagnostics and the sidebar."""
    return stores.for_node(node_id).path


__all__ = ['RoadmapStores', 'path_of']
