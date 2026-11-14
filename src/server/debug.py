"""On-demand, revision-cached diagnostics owned by the service."""

from __future__ import annotations

import asyncio
import logging
from copy import copy
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from src.engine.context.meter.debug import build_debug_report
from src.engine.context.meter.debug_wire import DEBUG_REPORT_ADAPTER
from src.engine.context.meter.request import RequestSnapshot

if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

    from src.engine.context.capability import ContextViewCapability
    from src.engine.providers.snapshot import ModelSnapshot
    from src.server.runtime import ServerRuntime

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

    async def read(self, runtime: ServerRuntime) -> dict[str, Any]:
        """Shield shared calculation from disconnects and exclusive TUI workers."""
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.create_task(self._read_snapshot(runtime))
        result = await asyncio.shield(self._inflight)
        if result['session_id'] != runtime.session.id:
            return await self.read(runtime)
        return result

    async def _read_snapshot(self, runtime: ServerRuntime) -> dict[str, Any]:
        session_runtime = runtime.session_runtime
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
