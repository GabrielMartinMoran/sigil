"""Choosing the block that renders a tool call."""

from __future__ import annotations

from typing import Any

from src.ui.widgets.chat.summon_block import SummonBlock
from src.ui.widgets.chat.tool_call_block import ToolCallBlock


def block_for(name: str, args: Any, *, classes: str, expanded: bool = False) -> ToolCallBlock:
    """The block a tool call gets. Only `summon` reads as a spawn."""
    if name == 'summon':
        return SummonBlock(name, args, classes=classes, expanded=expanded)
    return ToolCallBlock(name, args, classes=classes, expanded=expanded)
