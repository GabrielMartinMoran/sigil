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

from src.core.context.meter.debug_render import render_debug_report
from src.core.context.strategy.view_result import ViewResult
from src.server.runtime.server_runtime import ServerRuntime
from tests.support.server_parity import _composition, _runtime, _signed_in


@pytest.fixture
def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[ServerRuntime]:
    runtime = _runtime(_composition(tmp_path, monkeypatch))
    try:
        yield runtime
    finally:
        runtime.close()


def test_should_restore_full_debug_over_http_without_message_content(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def scenario() -> None:
        from src.core.context.meter.debug_snapshot import DEBUG_REPORT_ADAPTER

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
            client.headers['x-sigil-session'] = runtime.session.id
            runtime.session_runtime._run = None
            empty = (await client.get('/api/debug')).json()['report']
            assert empty['cache']['requests'] == 0
            assert empty['request']['captured'] is False

    asyncio.run(scenario())


def test_should_share_off_loop_calculation_and_survive_reader_cancellation(
    runtime: ServerRuntime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from src.server.launch.debug_diagnostics import DebugDiagnostics

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


def test_debug_node_scope_never_returns_parent_or_sibling_report(runtime: ServerRuntime, tmp_path: Path) -> None:
    from src.core.agents.profile.models.agent_role import AgentRole

    async def scenario() -> None:
        tree = runtime.composition.summoner.tree()
        root = tree.ensure_root('wizard')
        first = tree.add(parent_id=root.id, name='scout', role=AgentRole.SUBAGENT, task='read')
        second = tree.add(parent_id=root.id, name='reviewer', role=AgentRole.SUBAGENT, task='review')
        first.messages = [
            ModelRequest(parts=[UserPromptPart('private-child')]),
            ModelResponse(parts=[TextPart('child')], usage=RequestUsage(input_tokens=111, output_tokens=9)),
        ]
        second.messages = [
            ModelResponse(parts=[TextPart('other')], usage=RequestUsage(input_tokens=222, output_tokens=8))
        ]
        runtime.session.messages = [
            ModelResponse(parts=[TextPart('parent')], usage=RequestUsage(input_tokens=900, output_tokens=10))
        ]
        async with _signed_in(runtime, tmp_path) as client:
            for node_id, measured in [(first.id, 120), (second.id, 230), (None, 910), (first.id, 120)]:
                response = await client.get('/api/debug', params={'node_id': node_id} if node_id else {})
                assert response.status_code == 200, response.text
                data = response.json()
                assert data.get('node_id') == node_id
                assert data['report']['snapshot']['measured'] == measured
                assert 'private-child' not in response.text
                if node_id:
                    assert data['timing'] is None
                    assert not data['report']['request']['captured']
            parallel = await asyncio.gather(
                client.get('/api/debug', params={'node_id': first.id}),
                client.get('/api/debug', params={'node_id': second.id}),
                client.get('/api/debug'),
            )
            assert [reply.json()['report']['snapshot']['measured'] for reply in parallel] == [120, 230, 910]
            missing = await client.get('/api/debug', params={'node_id': 'missing'})
            assert missing.status_code == 404

    asyncio.run(scenario())


def test_node_debug_resolves_only_within_selected_session(runtime: ServerRuntime, tmp_path: Path) -> None:
    from src.core.agents.profile.models.agent_role import AgentRole

    async def scenario() -> None:
        owner = runtime.create_resident(runtime.session_runtime)
        tree = owner.composition.summoner.tree()
        root = tree.ensure_root('wizard')
        child = tree.add(parent_id=root.id, name='scout', role=AgentRole.SUBAGENT, task='inspect')
        child.messages = [
            ModelResponse(parts=[TextPart('private-secondary')], usage=RequestUsage(input_tokens=111, output_tokens=9))
        ]
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.get('/api/debug', params={'node_id': child.id})
            assert response.status_code == 404
            response = await client.get(
                '/api/debug', params={'node_id': child.id}, headers={'x-sigil-session': owner.session.id}
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload['session_id'] == owner.session.id
            assert payload['node_id'] == child.id
            assert payload['report']['snapshot']['measured'] == 120
            assert 'private-secondary' not in response.text
            response = await client.get('/api/debug')
            assert response.status_code == 200, response.text
            assert response.json()['session_id'] == runtime.session.id
            assert response.json().get('node_id') is None

    asyncio.run(scenario())


def test_debug_cache_does_not_retain_evicted_session(runtime: ServerRuntime, tmp_path: Path) -> None:
    import gc
    import weakref

    async def scenario() -> None:
        owner = runtime.create_resident(runtime.session_runtime)
        session_id = owner.session.id
        reference = weakref.ref(owner)
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.get('/api/debug', headers={'x-sigil-session': session_id})
            assert response.status_code == 200, response.text
            with runtime.session_lease(runtime.session.id):
                await runtime.release_idle_sessions(grace=0)
            assert runtime.find_session(session_id) is None
            del owner
            gc.collect()
            assert reference() is None

    asyncio.run(scenario())
