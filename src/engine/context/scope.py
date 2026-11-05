"""Tracker of the directories the agent is operating in.

A subdirectory's `AGENTS.md` applies only when the agent works inside
that subdirectory. To know which instructions apply you have to observe the
paths the tools touch and keep the ancestor chain of each one.

The root is always in scope: its `AGENTS.md` applies to the whole session.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

PATH_TOOLS: Mapping[str, tuple[str, ...]] = {
    'read_file': ('path',),
    'write_file': ('path',),
    'patch_file': ('path',),
}
"""Tools that declare a workspace path and therefore widen the scope.

`run` stays out on purpose: it receives shell commands, not paths, and guessing
which directories it touches would be making information up.
"""


class ScopeTracker:
    """Set of active directories, with the root always present."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(os.path.realpath(str(root)))
        self._active: set[Path] = {self._root}

    @property
    def root(self) -> Path:
        return self._root

    def reset(self) -> None:
        """Goes back to the minimal scope: root only."""
        self._active = {self._root}

    def rebind(self, root: str | Path) -> None:
        """Moves the scope to another workspace and drops the observed directories.

        The scope describes where the agent is working *now*, so a switch starts
        from the new root: a directory of the previous workspace inside the scope
        would keep its instructions alive for no reason.
        """
        target = Path(os.path.realpath(str(root)))
        if target == self._root:
            return
        self._root = target
        self._active = {target}

    def observe(self, tool_name: str, args: Mapping[str, object] | None) -> bool:
        """Records the paths of a call. Returns ``True`` if the scope changed."""
        if not args:
            return False
        arg_names = PATH_TOOLS.get(tool_name)
        if not arg_names:
            return False

        before = len(self._active)
        for arg_name in arg_names:
            raw = args.get(arg_name)
            if isinstance(raw, str) and raw.strip():
                self._add(raw)
        return len(self._active) != before

    def _add(self, raw_path: str) -> None:
        """Adds the path's directory and its whole ancestor chain up to the root."""
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self._root / candidate
        try:
            resolved = Path(os.path.realpath(str(candidate)))
            resolved.relative_to(self._root)
        except OSError, ValueError:
            # Outside the workspace: it contributes no instructions from this tree.
            return

        # A path can be a directory that does not exist yet (write_file creates
        # parents). The path is used as is if it does not exist, and its parent if it is a file.
        directory = resolved if resolved.is_dir() else resolved.parent
        while True:
            self._active.add(directory)
            if directory == self._root:
                break
            parent = directory.parent
            if parent == directory:
                break
            directory = parent

    def scopes(self) -> tuple[Path, ...]:
        """Active directories, root downwards (the child takes precedence)."""
        return tuple(sorted(self._active, key=lambda path: (len(path.parts), str(path))))

    def relative_scopes(self) -> tuple[str, ...]:
        """The scopes as paths relative to the root, to show in the UI."""
        result = []
        for path in self.scopes():
            relative = path.relative_to(self._root)
            result.append('.' if str(relative) == '.' else str(relative))
        return tuple(result)

    def observe_many(self, calls: Iterable[tuple[str, Mapping[str, object] | None]]) -> bool:
        changed = False
        for tool_name, args in calls:
            changed = self.observe(tool_name, args) or changed
        return changed


class ScopeSink:
    """`ToolLoggerHub` sink that feeds the tracker on every call.

    It hooks into the hub instead of each tool: that way it also sees the MCP tools and
    the ones registered later, without touching their signatures.
    """

    def __init__(self, tracker: ScopeTracker) -> None:
        self._tracker = tracker

    def __call__(self, event: object) -> None:
        if getattr(event, 'phase', None) != 'start':
            return
        tool_name = getattr(event, 'tool_name', '')
        args = getattr(event, 'args', None)
        if isinstance(tool_name, str) and isinstance(args, Mapping):
            self._tracker.observe(tool_name, args)
