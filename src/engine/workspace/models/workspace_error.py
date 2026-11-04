"""Failure of a workspace operation, with a message meant for the user."""

from __future__ import annotations


class WorkspaceError(RuntimeError):
    """A workspace operation could not be completed.

    The message is shown as is in the chat: it states what failed and what the
    user can do about it. It never carries git output that might leak paths or
    credentials beyond the ones the user already typed.
    """
