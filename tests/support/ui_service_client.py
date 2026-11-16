"""Shared ui service client test support."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from src.core.agents.profile.models.agent_role import AgentRole
from src.core.composition import Composition
from src.core.mcp.deferred import build_manager
from src.server.app import create_app
from src.server.endpoint import Endpoint
from src.server.endpoint_publisher import EndpointPublisher
from src.server.event_hub import EventHub
from src.server.server_config import ServerConfig
from src.server.server_runtime import ServerRuntime
from src.ui.outgoing_image import OutgoingImage
from src.ui.service_client import ServiceClient
from tests.support.server_parity import _composition

PASSWORD = 'secret-pass'

"""What the browser logs in with; the client never uses it."""


TOKEN = 'run-token-that-must-never-surface'

"""The bearer token this run publishes. The assertions look for exactly this."""


# --- the server under test ---------------------------------------------------


def _app(runtime: ServerRuntime, tmp_path: Path) -> FastAPI:
    """The real application, publishing the known token so a bearer client works."""
    publisher = EndpointPublisher(tmp_path / 'service' / 'endpoint.json', token=TOKEN)
    return create_app(runtime, ServerConfig(password=PASSWORD), dist=tmp_path / 'missing', publisher=publisher)


def _endpoint(*, token: str = TOKEN) -> Endpoint:
    """What the endpoint file would have carried. The port is never dialled here."""
    return Endpoint(host='sigil.test', port=80, pid=os.getpid(), token=token, started_at=0.0)


@asynccontextmanager
async def _served(
    runtime: ServerRuntime,
    tmp_path: Path,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[ServiceClient]:
    """A client wired to the real app, unless the test brought its own transport."""
    chosen = transport or httpx.ASGITransport(app=_app(runtime, tmp_path), raise_app_exceptions=False)
    client = ServiceClient(_endpoint(), transport=chosen)
    client._select_session({'id': runtime.session.id})
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Composition:
    return _composition(tmp_path, monkeypatch)


@pytest.fixture
def runtime(composition: Composition) -> ServerRuntime:
    return ServerRuntime(composition, EventHub(), require_ownership=False)


# --- transports a test supplies ----------------------------------------------


class _RecordingTransport(httpx.AsyncBaseTransport):
    """The ASGI transport, plus the requests the client actually put on the wire."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return await self._inner.handle_async_request(request)


class _Chunks(httpx.AsyncByteStream):
    """A response body read chunk by chunk, so a cut can happen anywhere."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _ScriptedTransport(httpx.AsyncBaseTransport):
    """One prepared reply per request, in order. Records what was asked."""

    def __init__(self, *replies: httpx.Response) -> None:
        self.requests: list[httpx.Request] = []
        self._replies = list(replies)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._replies.pop(0)


class _Unreachable(httpx.AsyncBaseTransport):
    """A port nothing listens on."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError('Connection refused', request=request)


def _stream(chunks: list[bytes], *, status: int = 200) -> httpx.Response:
    return httpx.Response(status, headers={'content-type': 'text/event-stream'}, stream=_Chunks(chunks))


def _json(status: int, payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status, json=payload)


# --- fixtures the terminal sends ---------------------------------------------


def _png(color: tuple[int, int, int] = (255, 0, 0), size: tuple[int, int] = (4, 4)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', size, color).save(buffer, format='PNG')
    return buffer.getvalue()


def _image(color: tuple[int, int, int]) -> OutgoingImage:
    return OutgoingImage(media_type='image/png', data=base64.b64encode(_png(color)).decode('ascii'))


# --- MCP: the layer is fenced off from the developer's home config ------------


def _mcp_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ServerRuntime:
    """One configured server and an installed layer, with `~/.sigil` fenced out.

    `config_paths` falls back to the home directory, so without `SIGIL_CONFIG` the
    list this asserts would depend on the machine the suite ran on.
    """
    config = tmp_path / '.sigil' / 'config.json'
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({'mcp_servers': {'probe': {'command': 'npx', 'prefix': ''}}}), encoding='utf-8')
    monkeypatch.setenv('SIGIL_CONFIG', str(config))
    composition = _composition(tmp_path, monkeypatch)
    composition.mcp.install(build_manager(composition.mcp_config.servers, None))
    return ServerRuntime(composition, EventHub(), require_ownership=False)


# --- the model catalogue, the rollback and the fork ---------------------------
#
# The catalogue is a network call on the provider's side, so the tests inject a
# deterministic double: no credential and no socket are available here, and a real
# fetch would make the assertion depend on someone's plan. Everything else is the
# real application.


class _Catalog:
    """Stands in for `ModelCatalogService`: answers, or never does."""

    def __init__(self, models: tuple[Any, ...] = (), *, hangs: bool = False) -> None:
        self._models = models
        self._hangs = hangs

    async def models(self, account: Any, *, secret: str | None = None) -> Any:
        if self._hangs:
            await asyncio.Event().wait()
        return self._catalog()

    def cached(self, account: Any) -> Any:
        """`ModelService.select` consults the cache for the model's capability."""
        return self._catalog()

    def _catalog(self) -> Any:
        from src.core.providers.domain.model_catalog import ModelCatalog

        return ModelCatalog(models=self._models, source='remote')


def _with_catalog(runtime: ServerRuntime, catalog: Any) -> None:
    """Installs a catalogue double on the runtime's provider service."""
    runtime.composition.model_service.catalog = catalog


def _an_account(runtime: ServerRuntime) -> str:
    accounts = runtime.composition.model_service.accounts.load()
    assert accounts, 'the fixture configures one account'
    return accounts[0].alias


# --- provider accounts -------------------------------------------------------
#
# The credential check is a deterministic double: `tests/test_provider_service.py`
# builds the service with a `probe` that accepts any key, so nothing here reaches a
# provider and no real credential is needed.


def _without_secrets(payload: Any) -> str:
    import json as _json

    return _json.dumps(payload)


# --- worktrees, and the images a rollback gives back -------------------------


def _git_repo(root: Path) -> Path:
    """A one-commit repository, the minimum a worktree needs."""
    import subprocess

    root.mkdir(parents=True, exist_ok=True)
    for args in (('init', '-q', '-b', 'main'), ('config', 'user.email', 't@t'), ('config', 'user.name', 't')):
        subprocess.run(('git', *args), cwd=root, check=True, capture_output=True)
    (root / 'README.md').write_text('x\n', encoding='utf-8')
    for args in (('add', '.'), ('commit', '-qm', 'init')):
        subprocess.run(('git', *args), cwd=root, check=True, capture_output=True)
    return root


# --- the tree -----------------------------------------------------------------


def _subagent(runtime: ServerRuntime, *, mode: str = 'sync', task: str = 'Review the diff.') -> Any:
    """A node of the service's real tree, hanging from the session agent.

    The routes act on the tree the summoner owns, so the node is added there. A
    subagent `TestModel` really ran would be finished before the assertion read it,
    and "stop a running node" would then be a race instead of a contract.
    """
    return runtime.composition.agent_trees.active.add(
        parent_id=None,
        name='reviewer',
        role=AgentRole.SUBAGENT,
        task=task,
        mode=mode,
        model='test',
    )
