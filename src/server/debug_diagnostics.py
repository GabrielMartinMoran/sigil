"""On-demand, revision-cached diagnostics owned by the service."""

from __future__ import annotations

import asyncio
import logging
from copy import copy
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from src.core.context.meter.debug import build_debug_report
from src.core.context.meter.debug_snapshot import DEBUG_REPORT_ADAPTER
from src.core.context.meter.node_debug import node_debug_report
from src.core.context.meter.request_snapshot import RequestSnapshot

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

    from src.core.context.context_view_capability import ContextViewCapability
    from src.core.providers.model_snapshot import ModelSnapshot
    from src.server.server_runtime import ServerRuntime
    from src.server.session_runtime import SessionRuntime

logger = logging.getLogger(__name__)


def _build_report(
    history: list[ModelMessage], capability: ContextViewCapability, request: RequestSnapshot, model: ModelSnapshot
) -> dict[str, Any]:
    """CPU-only projection over a captured snapshot, never the live composition."""
    try:
        view = capability.project(history)
    except Exception:
        logger.exception('Debug projection failed')
        view = capability.last_view
    report = build_debug_report(
        history,
        snapshot=capability.meter.snapshot(history),
        request=request,
        view_result=view,
        effort=model.thinking,
        model=model.label,
    )
    return DEBUG_REPORT_ADAPTER.dump_python(report, mode='json')


class DebugDiagnostics:
    """One cached report and at most one calculation, shared by attached clients."""

    def __init__(self) -> None:
        self._key: tuple[object, ...] | None = None
        self._report: dict[str, Any] | None = None
        self._inflight: asyncio.Task[dict[str, Any]] | None = None

    async def read(self, runtime: ServerRuntime | SessionRuntime, node_id: str | None = None) -> dict[str, Any]:
        """Shield shared calculation from disconnects and exclusive TUI workers."""
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.create_task(self._read_snapshot(runtime, node_id))
        result = await asyncio.shield(self._inflight)
        if result['session_id'] != runtime.session.id or result.get('node_id') != node_id:
            return await self.read(runtime, node_id)
        return result

    async def _read_snapshot(
        self, runtime: ServerRuntime | SessionRuntime, node_id: str | None = None
    ) -> dict[str, Any]:
        if node_id is not None:
            node = runtime.node(node_id)
            messages = node.run.all_messages() if node.run is not None else node.messages
            owner_session_id = runtime.session.id
            key = (
                owner_session_id,
                node.id,
                runtime.hub.last_seq,
                id(node.messages),
                len(messages),
                id(runtime.composition.context_capability.request_for_node(node.id)),
            )
            if key != self._key or self._report is None:
                history = [replace(message, parts=[copy(part) for part in message.parts]) for message in messages]
                snapshot = replace(node, messages=history, run=None)
                capability = copy(runtime.composition.context_capability)
                capability.strategy = capability._strategy_for_now()
                capability.provider = None
                report = await asyncio.to_thread(node_debug_report, snapshot, capability)
                self._report = DEBUG_REPORT_ADAPTER.dump_python(report, mode='json')
                self._key = key
            return {'session_id': owner_session_id, 'node_id': node.id, 'report': self._report, 'timing': None}
        session_runtime = getattr(runtime, 'session_runtime', runtime)
        composition = runtime.composition
        capability = composition.context_capability
        model = composition.model_service.snapshot()
        session = runtime.session
        messages = session_runtime._run.all_messages() if session_runtime._run is not None else session.messages
        key = (
            session.id,
            id(session.messages),
            len(messages),
            runtime.hub.last_seq,
            id(capability.last_request),
            composition.meter.budget(),
            model.label,
            model.thinking,
        )
        if key != self._key or self._report is None:
            # Streaming mutates envelopes and part fields. Copy those on the owner
            # loop; payloads are already produced values, not mutable run state.
            history = [replace(message, parts=[copy(part) for part in message.parts]) for message in messages]
            captured = copy(capability)
            captured.meter = copy(composition.meter)
            captured.strategy = capability._strategy_for_now()
            captured.provider = None
            request = capability.last_request or RequestSnapshot()
            request = replace(request, instructions=tuple(replace(row, preview='') for row in request.instructions))
            self._report = await asyncio.to_thread(_build_report, history, captured, request, model)
            self._key = key
        timing = session_runtime._turn_timings
        return {
            'session_id': session.id,
            'report': self._report,
            'timing': timing.render()
            if timing is not None and session_runtime._timing_session_id == session.id
            else None,
        }
