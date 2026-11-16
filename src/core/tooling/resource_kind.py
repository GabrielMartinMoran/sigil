"""The ResourceKind value of the tooling layer."""

from __future__ import annotations

from enum import StrEnum


class ResourceKind(StrEnum):
    """How the resource value is normalized before it meets a pattern."""

    TEXT = 'text'
    """Matched as given: a search query, a skill name, a server name."""

    PATH = 'path'
    """Matched as a path, both workspace-relative and absolute."""

    COMMAND = 'command'
    """Matched as a shell command, with its whitespace collapsed."""
