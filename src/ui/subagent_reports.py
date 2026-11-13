"""Where a persisted subagent report is recognized, for the terminal.

The rule lives in the engine (`src.engine.agents.tree.reports`): it is about the
envelope a turn persists, not about a widget, and both the projection the service
sends and the in-process renderer have to apply the same one. This module is the
terminal's name for it, kept so the renderer does not reach across the boundary for
a sentence it already imports.
"""

from __future__ import annotations

from src.engine.agents.tree.reports import WAKE_PROMPT, split_reports

__all__ = ['WAKE_PROMPT', 'split_reports']
