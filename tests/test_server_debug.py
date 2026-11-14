"""Full debug contract: service measurements, no conversation payloads."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.usage import RequestUsage

from src.engine.context.meter.debug_render import render_debug_report
from src.engine.context.strategy.view_result import ViewResult
from src.server.runtime import ServerRuntime
from tests.test_server_parity import _composition, _runtime, _signed_in


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ServerRuntime]:
    runtime = _runtime(_composition(tmp_path, monkeypatch))
    try:
        yield runtime
    finally:
        runtime.close()


def test_should_restore_full_debug_over_http_without_message_content(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def scenario() -> None:
        from src.engine.context.meter.debug_wire import DEBUG_REPORT_ADAPTER

        await runtime.session_runtime._turn('private-debug-sentinel')
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.get('/api/debug')
            assert response.status_code == 200, response.text
            payload = response.json()
            report = DEBUG_REPORT_ADAPTER.validate_python(payload['report'])
            text = render_debug_report(report)
            for section in (
                'Fixed cost per request',
                'Last request',
                'Prompt cache',
                'Turns',
                'Maintenance',
                'History composition',
            ):
                assert section in text
            assert report.request.captured
            assert report.request.tools
            assert report.cache.requests > 0
            assert report.has_view
            assert 'private-debug-sentinel' not in response.text
            assert 'Turn timing: total' in payload['timing']
            assert 'ModelRequestNode:' in payload['timing']
            assert 'first event' in payload['timing']
            assert 'messages' not in payload['report']['view_result']
            assert 'summary' not in payload['report']['view_result']
            assert all(not part.preview for row in report.log for part in row.parts)
            assert all(not block.preview for block in report.request.instructions)

    asyncio.run(scenario())


def test_should_read_live_run_and_cache_unchanged_diagnostics(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart('not-on-the-wire')]),
            ModelResponse(
                parts=[TextPart('private-output')],
                usage=RequestUsage(input_tokens=120, output_tokens=10, cache_read_tokens=100),
            ),
        ]
        runtime.session_runtime._run = SimpleNamespace(all_messages=lambda: messages)
        calls = 0
        original = runtime.composition.context_capability.project

        def project(history: list[ModelMessage]) -> ViewResult:
            nonlocal calls
            calls += 1
            return original(history)

        monkeypatch.setattr(runtime.composition.context_capability, 'project', project)
        async with _signed_in(runtime, tmp_path) as client:
            first = (await client.get('/api/debug')).json()
            second = (await client.get('/api/debug')).json()
            assert first['report'] == second['report']
            assert calls == 1
            assert first['report']['cache']['cache_read'] == 100
            assert first['report']['snapshot']['measured'] == 130
            assert 'not-on-the-wire' not in json.dumps(first)
            runtime.hub.publish_state('turn')
            await client.get('/api/debug')
            assert calls == 2
            runtime.new_session()
            runtime.session_runtime._run = None
            empty = (await client.get('/api/debug')).json()['report']
            assert empty['cache']['requests'] == 0
            assert empty['request']['captured'] is False

    asyncio.run(scenario())


def test_should_share_off_loop_calculation_and_survive_reader_cancellation(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from src.server.debug import DebugDiagnostics

    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    calls = 0
    original = runtime.composition.context_capability.project

    def project(history: list[ModelMessage]) -> ViewResult:
        nonlocal calls
        calls += 1
        assert threading.get_ident() != owner
        entered.set()
        assert release.wait(timeout=5), 'test must release the projection'
        return original(history)

    monkeypatch.setattr(runtime.composition.context_capability, 'project', project)

    async def scenario() -> None:
        diagnostics = DebugDiagnostics()
        first = asyncio.create_task(diagnostics.read(runtime))
        second = None
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 5), timeout=6)
            second = asyncio.create_task(diagnostics.read(runtime))
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            # While the diagnostic worker is blocked, normal API traffic completes.
            async with _signed_in(runtime, tmp_path) as client:
                state = await asyncio.wait_for(client.get('/api/state'), timeout=2)
                assert state.status_code == 200
            release.set()
            result = await asyncio.wait_for(second, timeout=5)
            assert result['session_id'] == runtime.session.id
            assert calls == 1
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second is not None else []), return_exceptions=True)

    asyncio.run(scenario())
