"""Parity, over HTTP, for the commands the terminal already has.

These are contract tests. Each one drives the real ASGI app and then reads the
fact back — through `/api/state`, the transcript or the rows themselves — because
an endpoint that answers `200` and changes nothing is exactly the failure this
file exists to catch.

Three things are asserted per command:

- the response is the projection `views` defines, not a shape invented by the
  route;
- the state really moved, read back from the place that owns it;
- the command is refused while a turn runs, and the refusal leaves everything
  untouched — the same refusal the in-process path already gives.

The model is `TestModel` and the account is a seeded one, with no network: the
behaviour under test is the wiring between route, runtime and engine, not the
provider.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic_ai.models.test import TestModel

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.rollback import ForkOutcome, RollbackOutcome
from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.models.node_status import NodeStatus
from src.engine.bootstrap import Composition
from src.engine.context.budget import ContextBudget
from src.engine.context.meter.cost import SessionCost, session_cost
from src.engine.context.meter.snapshot import ContextSnapshot
from src.engine.providers.bootstrap import ProviderPaths
from src.engine.settings import load_settings
from src.engine.storage.accepted_inputs import AcceptedInputs
from src.server import views
from src.server.app import create_app
from src.server.config import ServerConfig
from src.server.events import EventHub
from src.server.runtime import ServerRuntime
from tests.test_agents import write_profile
from tests.test_provider_service import _add, _service

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
    monkeypatch.setattr('src.engine.bootstrap.build_service', lambda *args, **kwargs: service)
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
    async with httpx.AsyncClient(transport=transport, base_url='http://sigil.test') as client:
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
    runtime._task = asyncio.create_task(pending.wait())
    try:
        yield
    finally:
        task = runtime._task
        assert task is not None
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        runtime._task = None


async def _one_turn(runtime: ServerRuntime, client: httpx.AsyncClient, text: str = 'first') -> None:
    """Accepts a message over HTTP and waits for the turn it started."""
    response = await client.post('/api/turn', json={'text': text})
    assert response.status_code == 200, response.text
    await asyncio.wait_for(runtime.wait(), 5)


# --- a session that has not been written yet ---------------------------------


def test_a_session_with_no_row_answers_a_typed_404(runtime: ServerRuntime, tmp_path: Path) -> None:
    """A draft session has no durable row, and that is a 404 a client can read.

    It used to be a 500 — an unhandled ``LookupError`` — which the terminal painted as
    "the terminal and the service are different versions" over an empty conversation.
    A conversation that has not started is a normal state, so the route says so in the
    one vocabulary every client already has: a status, with the reason in the body.
    """

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.get('/api/session/durable')

            assert response.status_code == 404, response.text
            assert 'No session' in response.json()['error']
            # The session exists — it just has nothing in it. The state still answers,
            # and that is what tells a client this is "no history", not "no service".
            assert (await client.get('/api/state')).status_code == 200

            # And the answer is about the draft, not about the route: one turn later the
            # same read succeeds and carries what was said.
            await _one_turn(runtime, client)
            again = await client.get('/api/session/durable')
            assert again.status_code == 200, again.text
            assert [item['text'] for item in again.json()['transcript'] if item['kind'] == 'user'] == ['first']

    asyncio.run(go())


# --- the model --------------------------------------------------------------


def test_the_model_command_changes_the_model_and_the_session(runtime: ServerRuntime, tmp_path: Path) -> None:
    """Changing the model is a service change *and* a session change."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post(
                '/api/model',
                json={'account': 'work', 'model': 'second/model', 'thinking': 'medium'},
            )
            assert response.status_code == 200, response.text

            service = runtime.composition.model_service
            assert response.json()['model'] == views.model_view(service.snapshot())

            # Read it back from the projection the client paints, not from the reply.
            state = (await client.get('/api/state')).json()
            assert state['model'] == views.model_view(service.snapshot())
            assert state['model']['model'] == 'second/model'
            assert state['model']['thinking'] == 'medium'

            # And from the row that survives a restart.
            assert runtime.session.selection is not None
            assert runtime.session.selection.model == 'second/model'
            assert str(runtime.session.selection.thinking) == 'medium'

    asyncio.run(go())


def test_the_model_command_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The same refusal the in-process path gets from `ModelService.select`."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            service = runtime.composition.model_service
            before = service.snapshot()
            selection = runtime.session.selection

            async with _busy(runtime):
                response = await client.post(
                    '/api/model',
                    json={'account': 'work', 'model': 'second/model'},
                )

            assert response.status_code == 409, response.text
            assert 'turn is in progress' in response.json()['error'].lower()
            assert service.snapshot() == before
            assert runtime.session.selection is selection

    asyncio.run(go())


# --- the effort -------------------------------------------------------------


def test_the_effort_command_changes_the_level_the_next_turn_uses(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post('/api/effort', json={'effort': 'high'})
            assert response.status_code == 200, response.text

            service = runtime.composition.model_service
            assert response.json()['model'] == views.model_view(service.snapshot())
            assert response.json()['model']['thinking'] == 'high'

            state = (await client.get('/api/state')).json()
            assert state['model']['thinking'] == 'high'
            assert runtime.session.selection is not None
            assert str(runtime.session.selection.thinking) == 'high'

    asyncio.run(go())


def test_the_effort_command_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            service = runtime.composition.model_service
            before = service.snapshot()

            async with _busy(runtime):
                response = await client.post('/api/effort', json={'effort': 'high'})

            assert response.status_code == 409, response.text
            assert 'turn is in progress' in response.json()['error'].lower()
            assert service.snapshot() == before

    asyncio.run(go())


def test_the_effort_command_rejects_a_level_the_model_does_not_accept(runtime: ServerRuntime, tmp_path: Path) -> None:
    """`max` is a real level; `first/model` accepts only low and high."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            service = runtime.composition.model_service
            before = service.snapshot()

            response = await client.post('/api/effort', json={'effort': 'max'})

            assert response.status_code == 400, response.text
            assert 'does not accept' in response.json()['error']
            assert service.snapshot() == before

    asyncio.run(go())


# --- the agent --------------------------------------------------------------


def test_the_agent_command_switches_the_profile_and_the_session(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post('/api/agent', json={'name': 'reviewer'})
            assert response.status_code == 200, response.text

            registry = runtime.composition.agent_registry
            assert response.json()['agent'] == views.agent_view(registry.active, active=True)
            assert response.json()['agent']['name'] == 'reviewer'

            state = (await client.get('/api/state')).json()
            assert state['agent'] == views.agent_view(registry.active, active=True)
            assert runtime.session.agent == 'reviewer'
            # The root of the tree follows the agent, or it would name the old one.
            assert runtime.composition.agent_trees.active.root.name == 'reviewer'

    asyncio.run(go())


def test_the_agent_command_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    """An agent swap changes the instructions and the tool set of a live run."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            registry = runtime.composition.agent_registry
            before = registry.active_name
            agent = runtime.session.agent

            async with _busy(runtime):
                response = await client.post('/api/agent', json={'name': 'reviewer'})

            assert response.status_code == 409, response.text
            assert 'turn is running' in response.json()['error'].lower()
            assert registry.active_name == before
            assert runtime.session.agent == agent

    asyncio.run(go())


def test_an_unknown_agent_is_refused_without_changing_the_active_one(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            registry = runtime.composition.agent_registry
            before = registry.active_name

            response = await client.post('/api/agent', json={'name': 'nobody'})

            assert response.status_code == 400, response.text
            assert registry.active_name == before

    asyncio.run(go())


# --- rollback ---------------------------------------------------------------


def test_the_rollback_points_are_the_view_and_are_listed_for_a_real_turn(
    runtime: ServerRuntime, tmp_path: Path
) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client)

            response = await client.get('/api/rollback')
            assert response.status_code == 200, response.text
            points = response.json()['points']

            checkpoint = runtime.composition.rollback.points(runtime.session.id)[0]
            # The route's own view, with the position read from the rows instead of
            # from the projection that computes it. A row assembled by hand here is
            # what drifted: it defaulted `seq` to `None` while the route projected the
            # prompt's position, and the test kept passing on a field it never checked.
            # Reading the position independently is what lets the two disagree.
            seq = runtime.composition.store.database.value(
                'SELECT seq FROM messages WHERE turn_id = ? AND payload LIKE ? ORDER BY seq LIMIT 1',
                (checkpoint.turn_id, f'%"part_kind":"{views.PROMPT_KIND}"%'),
            )
            assert points == [views.rollback_point_view(checkpoint, preview='first', seq=seq)]

    asyncio.run(go())


def test_the_rollback_command_drops_the_turn_and_rebinds_the_session(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The state change is in the rows: the turn's messages are gone."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client)
            root = runtime.session.root_id
            database = runtime.composition.store.database
            assert database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (root,)) > 0

            point = (await client.get('/api/rollback')).json()['points'][0]
            response = await client.post('/api/rollback', json={'turn_id': point['turn_id']})
            assert response.status_code == 200, response.text

            body = response.json()
            assert body['rollback']['target_turn_id'] == point['turn_id']
            assert body['rollback']['turns_dropped'] >= 1
            assert set(body['rollback']) == set(
                views.rollback_view(
                    RollbackOutcome(
                        target_turn_id='t',
                        turns_dropped=0,
                        executions_dropped=0,
                        messages_dropped=0,
                        files_restored=0,
                        files_deleted=0,
                        files_unavailable=False,
                        undo_checkpoint='u',
                    )
                )
            )
            assert body['session'] == views.session_view(runtime.session)
            assert database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (root,)) == 0

    asyncio.run(go())


def test_a_rollback_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client)
            point = (await client.get('/api/rollback')).json()['points'][0]
            root = runtime.session.root_id
            database = runtime.composition.store.database
            before = database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (root,))

            async with _busy(runtime):
                response = await client.post('/api/rollback', json={'turn_id': point['turn_id']})

            assert response.status_code == 409, response.text
            assert 'turn is running' in response.json()['error'].lower()
            assert database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (root,)) == before

    asyncio.run(go())


def test_a_rollback_of_an_unknown_turn_is_a_conflict(runtime: ServerRuntime, tmp_path: Path) -> None:
    """`RollbackError` from the engine is the same refusal the terminal reports."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post('/api/rollback', json={'turn_id': 'nope'})

            assert response.status_code == 409, response.text
            assert 'No turn' in response.json()['error']

    asyncio.run(go())


# --- fork -------------------------------------------------------------------


def test_the_fork_command_creates_a_session_with_the_prefix(runtime: ServerRuntime, tmp_path: Path) -> None:
    """A fork copies what came *before* the point, and leaves the original alone.

    Two turns, and the fork is taken at the second: forking at the first turn has
    an empty prefix by definition — there is nothing before the beginning — so the
    copy count would be zero and the test would prove nothing about the prefix.
    """

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client, 'first')
            await _one_turn(runtime, client, 'second')
            point = (await client.get('/api/rollback')).json()['points'][0]
            original = runtime.session.id

            response = await client.post('/api/fork', json={'turn_id': point['turn_id']})
            assert response.status_code == 200, response.text

            body = response.json()
            fork_id = body['fork']['session_id']
            assert fork_id != original
            assert body['fork']['turns_copied'] == 1
            assert body['fork']['messages_copied'] >= 1
            assert set(body['fork']) == set(
                views.fork_view(ForkOutcome(session_id='s', turns_copied=0, messages_copied=0, executions_copied=0))
            )
            # The fork exists as a session of its own, and this one did not move.
            forked = runtime.composition.store.load(fork_id)
            assert forked is not None
            assert forked.messages
            assert runtime.session.id == original

    asyncio.run(go())


def test_a_fork_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client)
            point = (await client.get('/api/rollback')).json()['points'][0]
            before = len(runtime.composition.store.list_sessions())

            async with _busy(runtime):
                response = await client.post('/api/fork', json={'turn_id': point['turn_id']})

            assert response.status_code == 409, response.text
            assert 'turn is running' in response.json()['error'].lower()
            assert len(runtime.composition.store.list_sessions()) == before

    asyncio.run(go())


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


def test_stopping_a_node_cancels_it_and_the_tree_says_so(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            node = _running_node(runtime.composition)

            response = await client.post('/api/tree/stop', json={'node_id': node.id})
            assert response.status_code == 200, response.text

            body = response.json()
            # The stopped node is still a live projection in this process, so its
            # `elapsed` moves between the response and this read. Same rule as the
            # background test: the clock leaves both sides, every other field stays.
            assert _untimed(body['node']) == _untimed(views.node_view(node))
            assert _untimed_tree(body['tree']) == _untimed_tree(views.tree_view(runtime.composition.agent_trees.active))
            assert body['node']['status'] == 'cancelled'
            assert 'stopped' in body['message']

            state = (await client.get('/api/state')).json()
            listed = next(item for item in state['tree']['nodes'] if item['id'] == node.id)
            assert listed['status'] == 'cancelled'

    asyncio.run(go())


def test_backgrounding_a_node_keeps_it_running_and_flips_the_mode(runtime: ServerRuntime, tmp_path: Path) -> None:
    """`b` in the tree: the node keeps working and answers later, instead of blocking."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            node = _running_node(runtime.composition)

            response = await client.post('/api/tree/background', json={'node_id': node.id})
            assert response.status_code == 200, response.text

            assert node.mode == 'async'
            assert node.status is NodeStatus.RUNNING
            # The node is still running, so `elapsed` grew between the response and
            # this read: it is the clock, and the projection is compared without it.
            assert _untimed(response.json()['node']) == _untimed(views.node_view(node))
            assert response.json()['node']['mode'] == 'async'

            state = (await client.get('/api/state')).json()
            listed = next(item for item in state['tree']['nodes'] if item['id'] == node.id)
            assert listed['mode'] == 'async'

    asyncio.run(go())


def test_a_tree_action_on_an_unknown_node_is_refused_without_touching_the_tree(
    runtime: ServerRuntime, tmp_path: Path
) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            before = views.tree_view(runtime.composition.agent_trees.active)

            for path in ('/api/tree/stop', '/api/tree/background'):
                response = await client.post(path, json={'node_id': 'nope'})

                assert response.status_code == 400, response.text
                assert 'No agent with id' in response.json()['error']

            assert _untimed_tree(views.tree_view(runtime.composition.agent_trees.active)) == _untimed_tree(before)

    asyncio.run(go())


def test_the_session_agent_cannot_be_stopped_or_backgrounded(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The root is not a subagent: the same refusal the terminal reports."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            root = runtime.composition.agent_trees.active.root
            assert root is not None
            before = views.tree_view(runtime.composition.agent_trees.active)

            for path in ('/api/tree/stop', '/api/tree/background'):
                response = await client.post(path, json={'node_id': root.id})

                assert response.status_code == 409, response.text
                assert 'not a subagent' in response.json()['error']

            assert root.status is NodeStatus.RUNNING
            assert root.mode == 'sync'
            assert _untimed_tree(views.tree_view(runtime.composition.agent_trees.active)) == _untimed_tree(before)

    asyncio.run(go())


def test_a_finished_node_reports_its_stop_and_refuses_the_background(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The two refusals are the summoner's own: `kill` reports, `detach` refuses."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            node = _running_node(runtime.composition)

            stopped = await client.post('/api/tree/stop', json={'node_id': node.id})
            assert stopped.status_code == 200, stopped.text

            again = await client.post('/api/tree/stop', json={'node_id': node.id})
            assert again.status_code == 200, again.text
            assert 'already finished' in again.json()['message']

            moved = await client.post('/api/tree/background', json={'node_id': node.id})
            assert moved.status_code == 409, moved.text
            assert 'already finished' in moved.json()['error']
            assert node.status is NodeStatus.CANCELLED

    asyncio.run(go())


# --- the transcript of a subagent -------------------------------------------


def test_a_subagent_transcript_is_the_nodes_own_messages(runtime: ServerRuntime, tmp_path: Path) -> None:
    """A node the summoner really ran, read back through the route.

    `views.node_view` already knows how to project a node's messages; this proves
    the route hands it a real transcript rather than an empty list, and that a
    unique prefix of the id is enough to name the node.
    """
    write_profile(tmp_path / '.sigil' / 'agents', 'helper', description='Helps.', extra='role: subagent\n')
    runtime.composition.agent_registry.refresh()

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await runtime.composition.summoner.summon(parent_id=None, name='helper', task='Say done.', mode='sync')
            node = next(item for item in runtime.composition.agent_trees.active.nodes() if not item.is_root)
            assert node.status is NodeStatus.DONE
            assert node.messages

            response = await client.get(f'/api/tree/node/{node.id[:8]}')
            assert response.status_code == 200, response.text

            body = response.json()
            # A subagent may still be running, so its `elapsed` is the clock again.
            assert _untimed(body['node']) == _untimed(views.node_view(node, include_messages=True))
            assert [item['text'] for item in body['node']['transcript']] == ['Say done.', 'done']

    asyncio.run(go())


def test_a_transcript_of_an_unknown_node_is_refused(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.get('/api/tree/node/nope')

            assert response.status_code == 400, response.text
            assert 'No agent with id' in response.json()['error']

    asyncio.run(go())


# --- the MCP toggle ---------------------------------------------------------


def test_the_mcp_toggle_is_remembered_and_projected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    composition = _inert_mcp(_composition(tmp_path, monkeypatch, mcp_servers={'probe': {'command': 'npx'}}))
    runtime = _runtime(composition)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            # A real session row, so the override has somewhere to survive.
            await _one_turn(runtime, client)

            response = await client.post('/api/mcp', json={'name': 'probe', 'enabled': False})
            assert response.status_code == 200, response.text

            body = response.json()
            assert body['session'] == views.session_view(runtime.session)
            assert body['session']['mcp_overrides'] == {'probe': False}
            # The shell and the command answer the same projection.
            assert body['mcp'] == (await client.get('/api/state')).json()['mcp']

            reloaded = composition.store.load(runtime.session.id)
            assert reloaded is not None
            assert reloaded.mcp_overrides == {'probe': False}

    asyncio.run(go())


def test_an_unknown_mcp_server_is_refused_without_changing_the_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    composition = _inert_mcp(_composition(tmp_path, monkeypatch, mcp_servers={'probe': {'command': 'npx'}}))
    runtime = _runtime(composition)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post('/api/mcp', json={'name': 'ghost', 'enabled': False})

            assert response.status_code == 400, response.text
            assert 'No MCP server named' in response.json()['error']
            assert runtime.session.mcp_overrides == {}

    asyncio.run(go())


# --- external files ---------------------------------------------------------


def test_the_external_files_toggle_is_remembered(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client)

            allowed = await client.post('/api/external-files', json={'allow': True})
            assert allowed.status_code == 200, allowed.text
            assert allowed.json()['session'] == views.session_view(runtime.session)
            assert allowed.json()['session']['external_files'] is True

            state = (await client.get('/api/state')).json()
            assert state['session']['external_files'] is True
            reloaded = runtime.composition.store.load(runtime.session.id)
            assert reloaded is not None
            assert reloaded.allow_external_files is True

            denied = await client.post('/api/external-files', json={'allow': False})
            assert denied.status_code == 200, denied.text
            assert denied.json()['session']['external_files'] is False

    asyncio.run(go())


def test_the_external_files_body_must_be_a_boolean(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The terminal refuses anything but allow/deny; the route anything but a bool."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            response = await client.post('/api/external-files', json={'allow': 'yes'})

            assert response.status_code == 422, response.text
            assert runtime.session.allow_external_files is False

    asyncio.run(go())


# --- the context meter ------------------------------------------------------


def test_the_context_route_is_the_meters_projection(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client)

            response = await client.get('/api/context')
            assert response.status_code == 200, response.text

            meter = runtime.composition.meter
            expected = views.context_view(
                meter.snapshot(runtime.session.messages),
                session_cost(runtime.session.messages),
            )
            assert response.json()['context'] == expected
            assert response.json()['context']['window'] == 100_000
            assert response.json()['context']['projected'] == expected['used']
            assert (response.json()['context']['used'] or 0) > 0

    asyncio.run(go())


def test_the_context_budget_keeps_what_the_session_measured(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The fixed blocks travel on the row, so a bound session does not report zero."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            runtime.session.tools_tokens = 1234
            runtime.session.instructions_tokens = 567
            runtime.composition.bind_session(runtime.session)

            context = (await client.get('/api/context')).json()['context']

            assert context['budget']['tools'] == 1234
            assert context['budget']['instructions'] == 567

    asyncio.run(go())


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


def test_the_mcp_toggle_is_refused_while_a_turn_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    composition = _inert_mcp(_composition(tmp_path, monkeypatch, mcp_servers={'probe': {'command': 'npx'}}))
    runtime = _runtime(composition)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client)

            async with _busy(runtime):
                response = await client.post('/api/mcp', json={'name': 'probe', 'enabled': False})

            assert response.status_code == 409, response.text
            assert 'turn is running' in response.json()['error'].lower()

            stored = composition.store.load(runtime.session.id)
            assert stored is not None
            assert stored.mcp_overrides == {}

    asyncio.run(go())


def test_the_mcp_toggle_is_refused_while_a_subagent_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The terminal's second criterion: disabling a server mid-run closes its tools."""

    composition = _inert_mcp(_composition(tmp_path, monkeypatch, mcp_servers={'probe': {'command': 'npx'}}))
    runtime = _runtime(composition)

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client)

            async with _subagents_busy(composition.summoner):
                response = await client.post('/api/mcp', json={'name': 'probe', 'enabled': False})

            assert response.status_code == 409, response.text
            assert 'subagent' in response.json()['error'].lower()

            stored = composition.store.load(runtime.session.id)
            assert stored is not None
            assert stored.mcp_overrides == {}

    asyncio.run(go())


def test_external_files_is_refused_while_a_turn_runs(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client)

            async with _busy(runtime):
                response = await client.post('/api/external-files', json={'allow': True})

            assert response.status_code == 409, response.text
            assert 'turn is running' in response.json()['error'].lower()

            stored = runtime.composition.store.load(runtime.session.id)
            assert stored is not None
            assert stored.allow_external_files is False

    asyncio.run(go())


# --- the context projection, cold -------------------------------------------


def test_a_cold_session_reports_zero_usage_and_not_unknown() -> None:
    """The sidebar rule: a known window with nothing measured is 0%, not `?`."""
    snapshot = ContextSnapshot(measured=None, projected=None, window=100_000, budget=ContextBudget(window=100_000))

    context = views.context_view(snapshot, SessionCost())

    assert context['used'] == 0
    assert context['percent'] == 0.0
    assert context['measured'] is None
    assert context['projected'] is None


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


def test_the_defaults_routes_project_both_files_and_keep_a_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read is the view; the write lands in the file the destination names."""
    composition = _defaults_composition(tmp_path, monkeypatch)
    runtime = _runtime(composition)
    project, user = composition.root / '.sigil' / 'config.json', tmp_path / 'home' / '.sigil' / 'config.json'
    first = {'account': 'work', 'model': 'first/model', 'effort': 'high'}
    second = {'account': 'work', 'model': 'second/model', 'effort': 'medium'}
    _config(project, {'agents': {'reviewer': first}})
    accounts = list(composition.model_service.list_accounts())

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            read = await client.get('/api/model-defaults')
            assert read.status_code == 200, read.text
            assert read.json()['defaults'] == views.model_defaults_view(runtime.model_defaults(), accounts=accounts)

            written = await client.post(
                '/api/model-defaults',
                json={'target': 'reviewer', 'scope': 'global', 'config': second},
            )
            assert written.status_code == 200, written.text
            assert written.json()['defaults'] == views.model_defaults_view(runtime.model_defaults(), accounts=accounts)

            assert json.loads(user.read_text(encoding='utf-8'))['agents']['reviewer'] == second
            # The file the request did not name is the file the request does not touch.
            assert json.loads(project.read_text(encoding='utf-8')) == {'agents': {'reviewer': first}}

            again = await client.get('/api/model-defaults')
            assert again.json() == written.json()

    asyncio.run(go())


def test_a_defaults_write_is_refused_while_a_turn_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The rule the terminal applies is carried over HTTP, not decided again."""
    composition = _defaults_composition(tmp_path, monkeypatch)
    runtime = _runtime(composition)
    project = composition.root / '.sigil' / 'config.json'

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            async with _busy(runtime):
                response = await client.post(
                    '/api/model-defaults',
                    json={
                        'target': 'reviewer',
                        'scope': 'project',
                        'config': {'account': 'work', 'model': 'first/model'},
                    },
                )

            assert response.status_code == 409, response.text
            assert 'turn is running' in response.json()['error'].lower()

    asyncio.run(go())

    assert not project.exists(), 'a refused write must not create the file'


def test_a_defaults_body_names_a_destination_and_a_level_sigil_has(runtime: ServerRuntime, tmp_path: Path) -> None:
    """A request that cannot be satisfied is answered `400`, before any file is read."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            unknown = await client.post('/api/model-defaults', json={'target': None, 'scope': 'user', 'config': None})
            assert unknown.status_code == 400, unknown.text
            assert 'destination' in unknown.json()['error'].lower()

            level = await client.post(
                '/api/model-defaults',
                json={
                    'target': None,
                    'scope': 'project',
                    'config': {'account': 'work', 'model': 'm', 'effort': 'turbo'},
                },
            )
            assert level.status_code == 400, level.text
            assert 'turbo' in level.json()['error']

    asyncio.run(go())


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


def test_the_transcript_route_pages_and_says_where_the_page_starts(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            for text in ('one', 'two', 'three'):
                await _one_turn(runtime, client, text)

            # No window: everything, and no cursor to continue from.
            whole = await _transcript(client)
            assert whole['total'] == len(whole['items'])
            assert whole['first_index'] == 0
            assert whole['has_more'] is False
            assert len(whole['items']) > 3

            newest = await _transcript(client, limit=2)
            assert newest['items'] == whole['items'][-2:]
            assert newest['items'][0]['index'] == whole['items'][-2]['index']
            assert newest['first_index'] == len(whole['items']) - 2
            assert newest['has_more'] is True
            assert newest['total'] == len(whole['items'])

    asyncio.run(go())


def test_walking_the_cursor_back_hands_over_the_whole_transcript_once(runtime: ServerRuntime, tmp_path: Path) -> None:
    """The property a client relies on: no hole, no repeat, and the same indexes."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            for text in ('one', 'two', 'three', 'four'):
                await _one_turn(runtime, client, text)
            whole = (await _transcript(client))['items']

            pages: list[list[dict[str, Any]]] = []
            before: int | None = None
            while True:
                page = await _transcript(client, limit=2, **({} if before is None else {'before': before}))
                pages.append(page['items'])
                if not page['has_more']:
                    break
                before = page['first_index']

            assert len(pages) > 2, 'the walk has to span more than two pages to prove anything'
            assert [item for page in reversed(pages) for item in page] == whole

    asyncio.run(go())


def test_a_cursor_of_zero_is_an_empty_oldest_page(runtime: ServerRuntime, tmp_path: Path) -> None:
    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client, 'first')
            total = (await _transcript(client))['total']

            page = await _transcript(client, limit=2, before=0)

            assert page['items'] == []
            assert page['has_more'] is False
            # The transcript is still that long: an empty page is the bottom, not a loss.
            assert page['total'] == total

    asyncio.run(go())


def test_a_cursor_past_the_end_answers_the_newest_page(runtime: ServerRuntime, tmp_path: Path) -> None:
    """A client that asks from a stale position still gets a usable page."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            for text in ('one', 'two'):
                await _one_turn(runtime, client, text)
            whole = (await _transcript(client))['items']

            page = await _transcript(client, limit=1, before=10_000)

            assert page['items'] == whole[-1:]
            assert page['first_index'] == len(whole) - 1
            assert page['has_more'] is True

    asyncio.run(go())


def test_an_unusable_limit_or_cursor_is_a_400_with_the_reason(runtime: ServerRuntime, tmp_path: Path) -> None:
    """A bad window is the request's fault, so it is a 400 and not a broken read."""

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client, 'first')
            cases: list[tuple[dict[str, Any], str]] = [
                ({'limit': 0}, 'limit'),
                ({'limit': -2}, 'limit'),
                ({'limit': 'many'}, 'limit'),
                ({'limit': 10_000}, 'limit'),
                ({'before': -1}, 'before'),
                ({'before': 'half'}, 'before'),
            ]
            for params, word in cases:
                response = await client.get('/api/session/transcript', params=params)
                assert response.status_code == 400, (params, response.text)
                assert word in response.json()['error'], (params, response.text)

    asyncio.run(go())


def test_the_waiting_rows_are_never_part_of_a_page(runtime: ServerRuntime, tmp_path: Path) -> None:
    """A queued message is not history: it cannot become an item or move an index.

    The newest page is the only one a waiting row could ride with, so the older one
    is read too: neither list carries it, and the two pages still reconstruct the
    tail of the transcript exactly.
    """

    async def go() -> None:
        async with _signed_in(runtime, tmp_path) as client:
            await _one_turn(runtime, client, 'first')
            store = runtime.composition.store
            request = str(uuid.uuid4())
            store.accept_input(runtime.session, request, 'waiting text', [], 'turn')
            # Claimed and not delivered: it stays queued, and the consumer only takes
            # `accepted` rows, so it cannot become a message behind the assertion.
            AcceptedInputs(store.database).transition(runtime.session.id, request, 'claimed', attempt_id='attempt')

            durable = (await client.get('/api/session/durable')).json()
            assert [entry['text'] for entry in durable['pending']] == ['waiting text']

            whole = (await _transcript(client))['items']
            assert 'waiting text' not in str(whole)

            newest = await _transcript(client, limit=1)
            older = await _transcript(client, limit=1, before=newest['first_index'])
            assert 'waiting text' not in str(newest['items'])
            assert 'waiting text' not in str(older['items'])
            assert older['items'] + newest['items'] == whole[-2:]

    asyncio.run(go())
