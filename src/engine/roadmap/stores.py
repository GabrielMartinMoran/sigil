"""One roadmap per execution of the session.

A roadmap is a plan, and a plan belongs to whoever is executing it. The Lead's plan
is not the engineer's: they answer different questions — "what is the squad doing"
against "which unit am I in" — and one list under both of them would be a list that
lies to one of the two.

So the store is per execution. The root's plan is keyed by the root execution's id,
which is derived from the session, and a subagent's by its own. The key is the node
id and not the profile name: two concurrent researchers are two different plans even
when they are the same kind of agent.

Unlike the loaded skills, a plan *is* stored — a task can be re-opened without a new
tool call, so the transcript cannot answer what the plan is now.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from src.engine.roadmap.store import RoadmapStore
from src.engine.storage.executions import root_id_for

if TYPE_CHECKING:  # pragma: no cover
    from src.engine.agents.tree.models.agent_node import AgentNode


class RoadmapStores:
    """The plan of each execution, keyed by node (``None`` for the root)."""

    def __init__(
        self,
        session_id: str | None = None,
        *,
        base_dir: str | Path | None = None,
        flush: Callable[[], None] | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._session_id = session_id
        self._flush = flush
        self._by_node: dict[str | None, RoadmapStore] = {}
        self._listeners: list[Callable[[], None]] = []

    # --- what the rest of the app asks for ---------------------------------

    def for_node(self, node_id: str | None) -> RoadmapStore:
        """The plan of one execution, loaded on first use."""
        store = self._by_node.get(node_id)
        if store is None:
            store = RoadmapStore(
                self.execution_id_of(node_id),
                base_dir=self._base_dir,
                flush=self._flush,
            )
            store.add_listener(self._notify)
            self._by_node[node_id] = store
        return store

    @property
    def root(self) -> RoadmapStore:
        return self.for_node(None)

    def execution_id_of(self, node_id: str | None) -> str | None:
        """The execution a node's plan belongs to.

        The root has no node id of its own at this level, so its execution is the one
        derived from the session — the same id the store writes for the root
        execution. Both sides agree before either has written a row.
        """
        if not self._session_id:
            return None
        if node_id is None:
            return root_id_for(self._session_id)
        return node_id

    # --- lifecycle ---------------------------------------------------------

    def bind_session(self, session_id: str | None) -> None:
        """Points every plan at another session and drops the stale ones.

        A session swap invalidates the execution ids, so the stores for the ones that
        were open are discarded: keeping them would leave the next session reading a
        plan that belongs to a conversation the user is no longer in.

        Re-binding the same session is a no-op. It happens on every mount and on
        every view change, and recreating the stores there would throw away live
        listeners — and the items of a plan that is still open.
        """
        if session_id == self._session_id:
            return
        self._session_id = session_id
        for store in self._by_node.values():
            store.remove_listener(self._notify)
        self._by_node.clear()
        self._notify()

    def discard(self, node_id: str) -> None:
        """Forgets an execution's plan. Called when the execution is gone for good."""
        store = self._by_node.pop(node_id, None)
        if store is not None:
            store.remove_listener(self._notify)
            store.forget()
            self._notify()

    def reload_all(self) -> None:
        """Re-reads every open plan. Called after a rollback rewrote the rows."""
        for store in list(self._by_node.values()):
            store.reload()

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


__all__ = ['RoadmapStores']
