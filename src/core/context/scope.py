"""Tracker of the directories the agent is operating in.

A subdirectory's `AGENTS.md` applies only when the agent works inside
that subdirectory. To know which instructions apply you have to observe the
paths the tools touch and keep the ancestor chain of each one.

The root is always in scope: its `AGENTS.md` applies to the whole session.
"""

from __future__ import annotations

"""Tools that declare a workspace path and therefore widen the scope.

`run` stays out on purpose: it receives shell commands, not paths, and guessing
which directories it touches would be making information up.
"""
