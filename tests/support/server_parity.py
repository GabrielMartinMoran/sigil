"""Shared server parity test support."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic_ai.models.test import TestModel

from src.core.agents.profile.models.agent_role import AgentRole
from src.core.agents.tree.models.agent_node import AgentNode
from src.core.bootstrap import Composition
from src.core.providers.bootstrap import ProviderPaths
from src.core.settings import load_settings
from src.server.app import create_app
from src.server.event_hub import EventHub
from src.server.server_config import ServerConfig
from src.server.server_runtime import ServerRuntime
from tests.support.agents import write_profile
from tests.support.provider_service import _add, _service

PASSWORD = 'secret-pass'


def _composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mcp_servers: dict[str, Any] | None = None,
) -> Composition:
    """A real composition with one configured account and a second selectable agent.

    The service is built by the provider test's own helper and injected, so the
    account exists without probing a network or storing a real credential.

    ``mcp_servers`` writes the config the gateway parses at construction. The
    layer is never loaded here — the app is driven without a lifespan — which is
    exactly the state a toggle has to answer honestly about.
    """
    service = _service(tmp_path)
    _add(service)
    monkeypatch.setattr('src.core.bootstrap.build_service', lambda *args, **kwargs: service)
    write_profile(tmp_path / '.sigil' / 'agents', 'reviewer', description='Reviews code.')
    if mcp_servers is not None:
        config = tmp_path / '.sigil' / 'config.json'
        config.write_text(json.dumps({'mcp_servers': mcp_servers}), encoding='utf-8')
        # `config_paths` falls back to `~/.sigil/config.json`, so without this the
        # servers a test sees would depend on the machine it ran on.
        monkeypatch.setenv('SIGIL_CONFIG', str(config))
    composition = Composition(
        tmp_path,
        settings=load_settings(),
        # `call_tools=[]`: every tool would be called, and `ask_user` waits for an
        # answer no test gives. Rollback and fork need a completed turn, not tools.
        model=TestModel(custom_output_text='done', call_tools=[]),
        provider_paths=ProviderPaths(tmp_path / 'providers'),
    )
    composition.meter.set_window(100_000)
    return composition


def _runtime(composition: Composition) -> ServerRuntime:
    return ServerRuntime(composition, EventHub(), require_ownership=False)


@pytest.fixture
def composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Composition:
    return _composition(tmp_path, monkeypatch)


@pytest.fixture
def runtime(composition: Composition) -> ServerRuntime:
    return _runtime(composition)


@asynccontextmanager
async def _signed_in(runtime: ServerRuntime, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    """A logged-in client for the real app, over the ASGI transport (no socket)."""
    app = create_app(runtime, ServerConfig(password=PASSWORD), dist=tmp_path / 'missing')
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url='http://sigil.test', headers={'x-sigil-session': runtime.session.id}
    ) as client:
        login = await client.post('/api/login', json={'password': PASSWORD})
        assert login.status_code == 200, login.text
        yield client


@asynccontextmanager
async def _busy(runtime: ServerRuntime) -> AsyncIterator[None]:
    """Makes the runtime report a running turn, deterministically.

    `busy` is a fact about a task, so the test gives it a real one that never
    finishes: no sleeps, no dependency on how fast `TestModel` answers.
    """
    pending = asyncio.Event()
    runtime.session_runtime._task = asyncio.create_task(pending.wait())
    try:
        yield
    finally:
        task = runtime.session_runtime._task
        assert task is not None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        runtime.session_runtime._task = None


async def _one_turn(runtime: ServerRuntime, client: httpx.AsyncClient, text: str = 'first') -> None:
    """Accepts a message over HTTP and waits for the turn it started."""
    response = await client.post('/api/turn', json={'text': text})
    assert response.status_code == 200, response.text
    await asyncio.wait_for(runtime.wait(), 5)


# --- the tree ---------------------------------------------------------------


def _running_node(composition: Composition) -> AgentNode:
    """A node of the real tree that is still working.

    The routes change the tree, not the run. A summoned node under `TestModel`
    finishes in microseconds, so "stop a running node" would race it and prove
    nothing; the node is added through the tree the summoner owns, which is the
    object `kill` and `detach` act on. A node the summoner really ran is the
    transcript test below.
    """
    return composition.agent_trees.active.add(
        parent_id=None,
        name='reviewer',
        role=AgentRole.SUBAGENT,
        task='Review the diff.',
        mode='sync',
        model='test',
    )


CLOCK_FIELD = 'elapsed'

"""The one field of a node projection that comes from the wall clock, not the tree."""


def _untimed(view: dict[str, Any]) -> dict[str, Any]:
    """A node projection with the clock-derived field removed.

    A running node's ``elapsed`` is ``time.time() - started_at``, so two reads of the
    very same untouched node a moment apart differ by a tenth of a second — a fact
    about the clock, not about the tree. Comparing projections is about state, so the
    clock leaves both sides; every other field is still compared, which is what keeps
    "the refusal did not touch the tree" from becoming a weaker assertion.

    Freezing ``started_at`` would not help: ``elapsed`` reads ``time.time()`` while the
    node is running, and stamping ``finished_at`` on it to freeze the difference would
    mark a node finished that is still working.
    """
    return {key: value for key, value in view.items() if key != CLOCK_FIELD}


def _untimed_tree(tree: dict[str, Any]) -> dict[str, Any]:
    """``views.tree_view`` with every node's clock-derived field removed."""
    return {**tree, 'nodes': [_untimed(node) for node in tree['nodes']]}


# --- busy refusals for the session settings --------------------------------
#
# The terminal refuses both toggles while a turn runs (they are in
# `BUSY_COMMANDS`), and refuses the MCP one while any subagent is still working,
# because disabling a server closes the transport under a run whose tool set was
# already computed. These are those two refusals, carried over HTTP.


@asynccontextmanager
async def _subagents_busy(summoner: Any) -> AsyncIterator[None]:
    """Makes the summoner report a live subagent, deterministically.

    `running()` counts live tasks, so the test gives it a real one that never
    finishes — the same trick `_busy` uses for the turn, because a `TestModel`
    subagent finishes in microseconds and would race the request.
    """
    pending = asyncio.Event()
    task = asyncio.create_task(pending.wait())
    summoner._tasks['probe'] = task
    try:
        yield
    finally:
        summoner._tasks.pop('probe', None)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _inert_mcp(composition: Composition) -> Composition:
    """Keeps the layer from starting while still answering whoever asks it.

    `_start_mcp` runs the real loader as soon as a turn is sent, which would
    spawn the configured stdio server. Returning `None` from `mcp_loader`
    leaves it unstarted, and `fail` releases the ready event so `describe()`
    answers an empty layer instead of waiting on a manager that is not coming.
    Deterministic by construction: no process, no sleep, no thread.
    """
    composition.mcp_loader = lambda: None
    composition.mcp.fail('the MCP layer is not started in this test')
    return composition


# --- the model defaults ------------------------------------------------------
#
# `/default-models` was the one command the terminal could not serve attached, because
# no route projected the two config files it edits. These are those routes: the read is
# `views.model_defaults_view`, and the write is the engine's, refused with its sentence.


def _defaults_composition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Composition:
    """The parity composition, with both defaults files inside the test's own home.

    `defaults_paths` resolves the user file from `~`, so `HOME` is redirected before
    the engine is built: the developer's own `~/.sigil/config.json` must not reach a
    projection a test asserts.
    """
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('SIGIL_CONFIG', raising=False)
    return _composition(tmp_path, monkeypatch)


def _config(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


# --- the transcript, one window at a time -----------------------------------
#
# The route answers the whole conversation unless a window is asked for, which is
# what the web client still needs. These tests drive the real app because the
# contract is a query contract: a page has to end where the cursor says, and
# walking the cursor back has to hand every item over exactly once, with the index
# it already had.


async def _transcript(client: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    response = await client.get('/api/session/transcript', params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --- the route pages the messages, not the items ----------------------------
#
# The route now counts what each message projects and builds only the window it
# returns, instead of projecting the whole conversation and cutting afterwards.
# Projecting and then paging is the definition of a correct page, so it stays
# here as the oracle: every window has to come back identical, not equivalent.


def _window_messages() -> list[Any]:
    """A transcript with multi-part messages, so a page can end inside one."""
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        SystemPromptPart,
        TextPart,
        ThinkingPart,
        ToolCallPart,
        ToolReturnPart,
        UserPromptPart,
    )

    return [
        ModelRequest(parts=[UserPromptPart('one')]),
        ModelResponse(
            parts=[
                ThinkingPart('t1'),
                TextPart('a1'),
                ToolCallPart(tool_name='read_file', args={'path': 'a.py'}, tool_call_id='c1'),
            ]
        ),
        ModelRequest(parts=[ToolReturnPart(tool_name='read_file', content='r1', tool_call_id='c1')]),
        ModelRequest(parts=[SystemPromptPart('system')]),
        ModelResponse(parts=[ThinkingPart('t2'), TextPart('a2')]),
        ModelRequest(parts=[UserPromptPart('two')]),
    ]
