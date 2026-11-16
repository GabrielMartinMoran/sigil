"""The ToolConflict value of the tooling layer."""

from __future__ import annotations


class ToolConflict(Exception):
    """The call cannot run alongside its siblings of the same response."""
