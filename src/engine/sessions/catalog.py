"""Sessions of every workspace of the repository, with the workspace they belong to.

Sessions live one folder per working directory. With worktrees, "the repository"
spans several directories, so the listing has to gather them together and say
which workspace each one belongs to; otherwise a session created in a worktree is
invisible from the main one.

Outside a git repository there is a single workspace and the catalog degrades to
exactly the old behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.engine.sessions.session_store import Session, SessionStore
from src.engine.workspace.models.worktree import MAIN_NAME, Workspace
from src.engine.workspace.repository import GitRepository


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """A session and the workspace it was created in."""

    session: Session
    workspace: Workspace
    is_current: bool

    @property
    def label(self) -> str:
        """Workspace label shown next to the session."""
        return self.workspace.label

    @property
    def is_worktree(self) -> bool:
        return self.workspace.is_worktree


def workspaces(root: str | os.PathLike[str]) -> list[Workspace]:
    """The active workspace and every other worktree of its repository.

    The active one always comes first, so the ordering of the picker does not
    depend on how git happens to list the worktrees.
    """
    current_root = Path(os.path.realpath(str(root)))
    repository = GitRepository.discover(current_root)
    if repository is None:
        return [Workspace(root=current_root)]

    current = Workspace(root=current_root, worktree=repository.worktree_containing(current_root))
    others = [
        Workspace(root=worktree.path, worktree=worktree)
        for worktree in repository.worktrees()
        if Path(os.path.realpath(str(worktree.path))) != current_root
    ]
    return [current, *others]


def catalog(root: str | os.PathLike[str], *, base_dir: str | Path | None = None) -> list[CatalogEntry]:
    """Every session of the repository's workspaces, newest first."""
    current_root = Path(os.path.realpath(str(root)))
    entries: list[CatalogEntry] = []

    for workspace in workspaces(current_root):
        store = SessionStore(cwd=workspace.root, base_dir=base_dir)
        is_current = workspace.root == current_root
        entries.extend(
            CatalogEntry(session=session, workspace=workspace, is_current=is_current)
            for session in store.list_sessions()
        )

    # Newest first across workspaces; the workspace name breaks ties so the order
    # stays stable between refreshes.
    entries.sort(key=lambda entry: (entry.session.updated_at, entry.label), reverse=True)

    # One session id is one row. A copy of a session file that ended up in two
    # workspaces' folders is the same conversation, and the listing is the input of
    # an `OptionList`: it refuses two options with the same id by raising, and that
    # exception inside `compose` is a crash, not a warning. The newest copy wins.
    unique: dict[str, CatalogEntry] = {}
    for entry in entries:
        unique.setdefault(entry.session.id, entry)
    return list(unique.values())


def label_for(root: str | os.PathLike[str]) -> str:
    """Workspace label of a directory: ``main``, the worktree name, or its folder."""
    current_root = Path(os.path.realpath(str(root)))
    repository = GitRepository.discover(current_root)
    if repository is None:
        return current_root.name or MAIN_NAME
    worktree = repository.worktree_containing(current_root)
    return worktree.label if worktree is not None else (current_root.name or MAIN_NAME)
