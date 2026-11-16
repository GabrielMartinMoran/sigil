"""The ToolGroup value of the tooling layer."""

from __future__ import annotations

from enum import StrEnum


class ToolGroup(StrEnum):
    """The coarse vocabulary a profile can rule on.

    One group per tool, chosen by what the tool *does* and not by the module it
    lives in: an agent that must not touch the disk denies ``read`` and ``write``
    and keeps the rest.
    """

    RUN = 'run'
    """Executes a process: the shell, and the scripts bundled inside a skill."""

    READ = 'read'
    """Reads a file. The path is the resource."""

    WRITE = 'write'
    """Creates or edits a file. The path is the resource."""

    WEB = 'web'
    """Reaches the network. The search query or the URL is the resource."""

    SKILLS = 'skills'
    """Lists, loads and reads Agent Skills and their bundled resources."""

    MCP = 'mcp'
    """The meta-tools over MCP servers, resources and prompts."""

    ASK = 'ask'
    """Talks to the user mid-turn."""

    ROADMAP = 'roadmap'
    """Publishes the task roadmap the UI shows."""

    SUMMON = 'summon'
    """Starts, resumes, steers and stops subagents: the tree, as one capability."""
