"""The Rule value of the agents layer."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from src.core.agents.profile.effect import Effect
from src.core.files.paths import workspace_root
from src.core.tooling.resource_kind import ResourceKind

_HOME_PREFIXES = ('~/', '$HOME/', '${HOME}/')


_WHITESPACE = re.compile(r'\s+')


@dataclass(frozen=True, slots=True)
class Rule:
    """One line of the policy: a scope, an effect and maybe resource patterns."""

    scope: str
    """``*``, a group, a tool name, or a glob over names."""

    effect: Effect
    """What happens when the scope (and the patterns) match."""

    patterns: tuple[str, ...] = ()
    """Resource patterns. Empty means the rule is about the whole tool."""

    def matches_tool(self, tool_name: str, groups: tuple[str, ...]) -> bool:
        """Whether the scope covers this tool, by name, group or wildcard."""
        if self.scope == '*':
            return True
        if self.scope in groups:
            return True
        return fnmatchcase(tool_name, self.scope)

    def matches_resource(self, values: tuple[str, ...], kind: ResourceKind, root: Path | None = None) -> bool:
        """Whether the call acts on something the rule talks about.

        A rule without patterns speaks about every resource. Otherwise *one* of
        the values has to match: ``run`` executes several commands and a single
        denied one is enough to refuse the call.
        """
        if not self.patterns:
            return True
        return any(_matches(pattern, value, kind, root) for pattern in self.patterns for value in values)


def _matches(pattern: str, value: str, kind: ResourceKind | str, root: Path | None = None) -> bool:
    try:
        resolved = ResourceKind(kind)
    except ValueError:
        resolved = ResourceKind.TEXT
    if resolved is ResourceKind.PATH:
        return _matches_path(pattern, value, root)
    if resolved is ResourceKind.COMMAND:
        return _matches_command(pattern, value)
    return fnmatchcase(value, pattern)


def _matches_command(pattern: str, command: str) -> bool:
    """Matches a command, with the ``git status *`` idiom for the bare form.

    A pattern ending in `` *`` also matches the command without arguments, so
    ``git status *`` covers both ``git status`` and ``git status --short``.
    Whitespace is collapsed on both sides: a rule is not a whitespace test.
    """
    normalized = _collapse(command)
    candidate = _collapse(pattern)
    if fnmatchcase(normalized, candidate):
        return True
    return candidate.endswith(' *') and fnmatchcase(normalized, candidate[:-2])


def _matches_path(pattern: str, value: str, root: Path | None = None) -> bool:
    """Matches a path, relative to the workspace or absolute.

    The pattern can be written either way — ``src/*.py``, ``./src/*.py``,
    ``$HOME/notes/*`` — and the value is tried in both forms, so the rule does
    not depend on how the model spelled the path it asked for.
    """
    root = workspace_root() if root is None else root
    candidates = _path_candidates(value, root)
    for form in _pattern_forms(pattern, root):
        if any(fnmatchcase(candidate, form) for candidate in candidates):
            return True
    return False


def _path_candidates(value: str, root: Path) -> tuple[str, ...]:
    absolute = _absolute(value, root)
    relative = _relative(absolute, root)
    return (absolute,) if relative is None else (relative, absolute)


def _pattern_forms(pattern: str, root: Path) -> tuple[str, ...]:
    expanded = _expand_home(pattern)
    if os.path.isabs(expanded):
        return (_normalize(expanded),)
    normalized = _normalize(expanded)
    return (normalized, _normalize(str(root / normalized)))


def _absolute(path: str, root: Path) -> str:
    expanded = _expand_home(path)
    if not os.path.isabs(expanded):
        expanded = str(root / expanded)
    return _normalize(expanded)


def _relative(absolute: str, root: Path) -> str | None:
    try:
        return Path(absolute).relative_to(root).as_posix()
    except ValueError:
        return None


def _expand_home(path: str) -> str:
    if path == '~' or path.startswith(_HOME_PREFIXES):
        return str(Path(os.path.expanduser(path.replace('${HOME}', '~').replace('$HOME', '~'))))
    return path


def _normalize(path: str) -> str:
    """Slash-separated, no ``..`` and no duplicate separators."""
    normalized = os.path.normpath(path).replace(os.sep, '/')
    return normalized


def _collapse(text: str) -> str:
    return _WHITESPACE.sub(' ', text).strip()
