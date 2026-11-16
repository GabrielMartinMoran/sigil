"""Diagnostics for one execution, without borrowing its parent's request data."""

from __future__ import annotations

import logging
from copy import copy

from src.core.agents.tree.models.agent_node import AgentNode
from src.core.context.context_view_capability import ContextViewCapability
from src.core.context.meter.context_meter import ContextMeter
from src.core.context.meter.debug import build_debug_report
from src.core.context.meter.debug_report import DebugReport
from src.core.context.meter.request_snapshot import RequestSnapshot


def node_debug_report(node: AgentNode, capability: ContextViewCapability | None) -> DebugReport:
    history = list(node.run.all_messages() if node.run is not None else node.messages)
    request = capability.request_for_node(node.id) if capability is not None else None
    request = request or RequestSnapshot()
    budget = capability.budget_for_node(node.id) if capability is not None else None
    measured = ContextMeter(
        window=budget.window if budget is not None else None,
        **(
            {'output_reserve': budget.output_reserve, 'safety_margin_percent': budget.safety_margin_percent}
            if budget
            else {}
        ),
    )
    measured.set_overhead(instructions=request.instructions_tokens, tools=request.tools_tokens)
    view = None
    if capability is not None:
        captured = copy(capability)
        captured.meter = measured
        try:
            view = captured.project(history)
        except Exception:
            logging.getLogger(__name__).exception('Subagent debug projection failed')
    return build_debug_report(
        history, snapshot=measured.snapshot(history), request=request, view_result=view, model=node.model or None
    )
