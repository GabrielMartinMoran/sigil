"""One action a tree row offers."""

from __future__ import annotations

from dataclasses import dataclass

from src.ui.widgets.tree_action_kind import TreeActionKind


@dataclass(frozen=True, slots=True)
class TreeAction:
    """A decision about one node, returned by the screen."""

    kind: TreeActionKind
    node_id: str
