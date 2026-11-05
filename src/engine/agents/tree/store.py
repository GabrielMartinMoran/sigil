"""The tree of the session in effect, and the only thing that swaps it.

A tree belongs to a session the way the history does: `/load` brings back the
subagents that session summoned, and `/new` starts from a root with nothing under
it. Everything else in the app — the summoner, the tree view, the chat — asks this
store for the tree instead of holding one, so switching sessions has exactly one
place to happen.

The store does not save. The tree hangs off ``Session.tree``, so the next
``SessionStore.save`` writes it with the rest of the conversation; there is no
second file to keep in sync.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.engine.agents.tree.tree import AgentTree

if TYPE_CHECKING:
    from src.engine.sessions.session_store import Session


class AgentTreeStore:
    """Points the app at the tree of the active session."""

    def __init__(self) -> None:
        self._tree = AgentTree()

    @property
    def active(self) -> AgentTree:
        """The tree of the session in effect. Never ``None``, possibly empty."""
        return self._tree

    def bind(self, session: Session, *, root_name: str = '') -> AgentTree:
        """Makes ``session``'s tree the active one, creating the root if needed.

        A session saved before trees existed has none, and neither has a new one:
        both get a root for the agent that is about to answer. The name is passed
        instead of read from the registry because the store knows nothing about
        profiles, and the caller does.
        """
        tree = session.tree if isinstance(session.tree, AgentTree) else AgentTree()
        session.tree = tree
        tree.ensure_root(root_name)
        self._tree = tree
        return tree

    def rename_root(self, name: str) -> None:
        """Keeps the root's name in step with the agent in effect."""
        if name:
            self._tree.ensure_root(name)


__all__ = ['AgentTreeStore']
