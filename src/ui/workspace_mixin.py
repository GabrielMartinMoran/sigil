"""Workspace switching: `/new --worktree`, `/load` from another worktree.

The mixin owns the sequence, not the git work: the `WorkspaceManager` creates and
switches, the app only decides *when*. The invariant is the same in every path:
nothing in memory changes until the workspace on disk is usable.
"""

from __future__ import annotations

from src.engine.workspace.manager import WorkspaceManager, WorkspaceSwitch
from src.engine.workspace.models.workspace_error import WorkspaceError
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.sidebar_panel import WorkspaceState


class WorkspaceMixin:
    """Keeps the app and its components pointed at the active workspace.

    It expects the app to define ``_workspace_manager``, ``_scope`` and
    ``_loaded_skills``.
    """

    _workspace_manager: WorkspaceManager | None
    _scope: object | None
    _loaded_skills: object | None

    def current_workspace_label(self) -> str:
        """Label of the active workspace, or ``-`` when switching is unavailable."""
        if self._workspace_manager is None:
            return '-'
        return self._workspace_manager.label or self._workspace_manager.root.name

    def workspace_state(self) -> WorkspaceState | None:
        """What the side panel shows: the tree the agent is working in.

        It is derived on demand instead of cached: the panel is repainted from
        several paths, and a stale label after a switch would be worse than the
        cost of reading the (already small) worktree list.
        """
        if self._workspace_manager is None:
            return None
        workspace = self._workspace_manager.current()
        return WorkspaceState(
            label=workspace.label,
            branch=workspace.branch,
            is_worktree=workspace.is_worktree,
        )

    def switch_workspace(self, root: str) -> WorkspaceSwitch:
        """Moves to ``root``. Raises ``WorkspaceError`` if it cannot."""
        if self._workspace_manager is None:
            raise WorkspaceError('Workspace switching is not available in this session.')
        result = self._workspace_manager.switch(root)
        self._after_switch()
        return result

    def create_worktree_workspace(self, slug: str) -> WorkspaceSwitch:
        """Creates a worktree and moves there. Raises ``WorkspaceError`` if it cannot."""
        if self._workspace_manager is None:
            raise WorkspaceError('Workspace switching is not available in this session.')
        result = self._workspace_manager.create_and_switch(slug)
        self._after_switch()
        return result

    def _after_switch(self) -> None:
        """Resets what belongs to the previous workspace."""
        # The scope and the loaded skills describe the workspace that was left
        # behind: keeping them would inject instructions and skill bodies that do
        # not apply to the files the agent is about to touch.
        if self._scope is not None:
            self._scope.reset()
        if self._loaded_skills is not None:
            self._loaded_skills.reset()
        self.refresh_sidebar()

    async def announce_switch(self, switch: WorkspaceSwitch, chat: ChatLog) -> None:
        """Paints what changed, including what the new workspace does *not* have."""
        lines = [f'Workspace: {switch.previous.label} → {switch.workspace.label}', f'  root: {switch.workspace.root}']
        branch = switch.workspace.branch
        if branch:
            lines.append(f'  branch: {branch}')
        if switch.created is not None:
            lines.append(f'  worktree created from {switch.created.short_head}')
            lines.append('  note: uncommitted changes of the previous workspace do not carry over')
            lines.append('  note: ignored files (.env, .venv, .sigil/config.json) are not copied')
        await chat.add_notice('\n'.join(lines), style='green')
