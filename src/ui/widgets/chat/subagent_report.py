"""The block that shows a subagent report."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from src.ui.messages.node_opened import NodeOpened


class SubagentReport(Static):
    """A compact, clickable record of a report delivered to the parent.

    Built from the three fields a projection carries, not from an `AgentNode`: an
    attached terminal holds no tree, and the row it paints comes from the service.
    """

    def __init__(self, *, label: str, short_id: str, node_id: str) -> None:
        super().__init__(Text(f'↩ {label} #{short_id} sent a report.', style='cyan'))
        self.node_id = node_id
        self.add_class('clickable')

    def on_click(self) -> None:
        self.post_message(NodeOpened(self.node_id))
