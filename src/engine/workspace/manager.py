"""The active workspace and the rebinding of everything that caches its root.

Several components resolve their storage from the working directory when they are
built: the session store, the roadmap, the scope, the workspace instructions and
the project skills. Switching workspace means re-pointing them at the new root,
not rebuilding them: the agent closes over the same instances, so their identity
must survive the switch.

The order matters. The repository work happens first (`git worktree add`) and the
process only moves (`chdir`) once that succeeded, so a failure leaves the session
and the repository exactly as they were.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.engine.paths import worktrees_dir as default_worktrees_dir
from src.engine.workspace.models.workspace_error import WorkspaceError
from src.engine.workspace.models.worktree import Workspace, Worktree
from src.engine.workspace.repository import GitRepository


class _Rebindable(Protocol):
    """A component whose root can be moved to another directory."""

    def rebind(self, root: str | Path) -> None: ...


class _SessionRebindable(Protocol):
    def rebind(self, cwd: str | Path) -> None: ...


class _RoadmapRebindable(Protocol):
    def rebind_root(self, root: str | Path) -> None: ...


@dataclass(frozen=True, slots=True)
class WorkspaceSwitch:
    """Outcome of a switch, with what the UI needs to announce it."""

    workspace: Workspace
    previous: Workspace
    created: Worktree | None = None

    @property
    def changed(self) -> bool:
        return self.workspace.root != self.previous.root


class WorkspaceManager:
    """Knows the active workspace and moves the process to another one.

    The components are optional so the manager can be built before the app and be
    tested on its own; one that is not bound is simply not re-pointed.
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        worktrees_dir: Path | None = None,
        session_store: _SessionRebindable | None = None,
        roadmap_store: _RoadmapRebindable | None = None,
        rebindables: list[_Rebindable] | None = None,
    ) -> None:
        self._root = Path(os.path.realpath(str(root if root is not None else Path.cwd())))
        self._worktrees_dir = worktrees_dir
        self._session_store = session_store
        self._roadmap_store = roadmap_store
        self._rebindables: list[_Rebindable] = list(rebindables or [])
        self.rebind_errors: list[str] = []
        """Diagnostics of the last switch: a component that could not be re-pointed."""

    # --- state -------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def worktrees_dir(self) -> Path:
        """Where new worktrees are created.

        It is resolved lazily, not in the constructor: `SIGIL_HOME` /
        `SIGIL_WORKTREES_DIR` are read from the environment, and a manager built
        before they were set has to honour them.
        """
        if self._worktrees_dir is not None:
            return self._worktrees_dir
        return default_worktrees_dir()

    @property
    def repository(self) -> GitRepository | None:
        """Repository of the active workspace, discovered from its own root."""
        return GitRepository.discover(self._root)

    def current(self) -> Workspace:
        """The active workspace, with the worktree that backs it if there is one.

        The lookup accepts a directory *inside* a worktree: Sigil can be started in a
        subdirectory, and it still works on that worktree's tree.
        """
        repository = self.repository
        worktree = repository.worktree_containing(self._root) if repository is not None else None
        return Workspace(root=self._root, worktree=worktree)

    @property
    def label(self) -> str:
        return self.current().label

    # --- creation ----------------------------------------------------------

    def create_worktree(self, slug: str) -> Worktree:
        """Creates a worktree under the worktrees directory. It does not activate it."""
        repository = self.repository
        if repository is None:
            raise WorkspaceError(f'The workspace {self._root} is not a git repository; worktrees need one.')
        return repository.create_worktree(slug, base_dir=self.worktrees_dir)

    def create_and_switch(self, slug: str) -> WorkspaceSwitch:
        """Creates a worktree and moves to it: the `/new --worktree` operation.

        Creating and moving are deliberately two steps: if the move fails, the
        worktree stays registered in git and the message says where it is, instead
        of leaving the repository and the session in an unknown state.
        """
        worktree = self.create_worktree(slug)
        return self.switch(worktree.path, created=worktree)

    # --- switching ---------------------------------------------------------

    def switch(self, root: str | os.PathLike[str], *, created: Worktree | None = None) -> WorkspaceSwitch:
        """Moves the process and every bound component to ``root``."""
        target = Path(os.path.realpath(str(root)))
        if not target.is_dir():
            raise WorkspaceError(f'Workspace directory does not exist: {target}')

        previous = self.current()
        if target != self._root:
            # `chdir` first: the tools resolve relative paths against the process
            # cwd, so the rebind below must describe the directory we are already in.
            previous_root = self._root
            try:
                os.chdir(target)
            except OSError as exc:
                raise WorkspaceError(f'Cannot switch to {target}: {exc}') from exc
            self._root = target
            self._rebind(previous_root)

        return WorkspaceSwitch(workspace=self.current(), previous=previous, created=created)

    def switch_to_worktree(self, worktree: Worktree) -> WorkspaceSwitch:
        """Moves to an existing worktree of the repository."""
        return self.switch(worktree.path)

    def _rebind(self, previous_root: Path) -> None:
        """Re-points the components that cached the old root.

        A component that fails to rebind is skipped instead of aborting: the
        switch already happened on disk, and each component re-resolves its root
        from `Path.cwd()` on the next use, so the session is degraded at worst.
        """
        if self._session_store is not None:
            self._session_store.rebind(self._root)
        if self._roadmap_store is not None:
            self._roadmap_store.rebind_root(self._root)
        for component in self._rebindables:
            try:
                component.rebind(self._root)
            except Exception as exc:  # noqa: BLE001 - see the docstring
                self.rebind_errors.append(f'{type(component).__name__}: {exc}')
