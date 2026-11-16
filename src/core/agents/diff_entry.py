"""The DiffEntry value of the agents layer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DiffEntry:
    """One file that differs between two snapshots."""

    status: str
    """`A` added, `M` modified, `D` deleted, relative to the second snapshot."""
    path: str
