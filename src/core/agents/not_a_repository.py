"""The NotARepository value of the agents layer."""

from __future__ import annotations


class NotARepository(Exception):
    """There is no git repository to snapshot, so the files half is unavailable."""
