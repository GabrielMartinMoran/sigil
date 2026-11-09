"""The tree of agents: roles, the tree itself, summoning and what comes back.

The feature has one idea and three consequences, so the tests are grouped that
way:

- **Roles.** A profile is either the agent the user selects or a subagent only a
  `summon` can start. The picker listens to the first, the tool to the second, and
  neither can be the other.
- **Summoning.** Sync blocks and returns the message as a tool result; async
  returns at once and the message arrives later as a user message. Both are the
  same run, so both are tested through a real `Agent.iter`.
- **Surviving.** A tree is written with its session and comes back with it: the
  conversation of a subagent is part of the conversation, and a resumed session
  that forgot what ran under it would be a worse record than the chat itself.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits

from src.engine.agents.agent import create_agent
from src.engine.agents.factory import AgentFactory, AgentSpec
from src.engine.agents.profile.models.agent_not_found import AgentNotFound
from src.engine.agents.profile.models.agent_parse_error import AgentParseError
from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.profile.parser import parse_profile
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.profile.source import ProfileSource
from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.models.node_status import NodeStatus
from src.engine.agents.tree.models.summon_error import SummonError
from src.engine.agents.tree.rendering import render_subagents, summonable
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SigilDeps, SubagentSummoner
from src.engine.agents.tree.tools import register_tree_tools
from src.engine.agents.tree.tree import AgentTree
from src.engine.sessions.session_store import SessionStore

FRONTMATTER = '---\nname: {name}\ndescription: {description}\n{extra}---\n{body}'


def write_profile(
    root: Path,
    name: str,
    *,
    description: str = '',
    extra: str = '',
    body: str = 'Be brief.',
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{name}.md'
    path.write_text(
        FRONTMATTER.format(name=name, description=description or f'Profile {name}.', extra=extra, body=body),
        encoding='utf-8',
    )
    return path


# --- roles -------------------------------------------------------------------


def test_a_profile_is_an_agent_unless_it_says_otherwise(tmp_path):
    write_profile(tmp_path, 'wizard')
    profile, _ = parse_profile(tmp_path / 'wizard.md', source='project')
    assert profile.role is AgentRole.AGENT
    assert profile.role.selectable


def test_a_profile_can_declare_itself_a_subagent(tmp_path):
    write_profile(tmp_path, 'researcher', extra='role: subagent\n')
    profile, _ = parse_profile(tmp_path / 'researcher.md', source='project')
    assert profile.role is AgentRole.SUBAGENT
    assert not profile.role.selectable


def test_an_unknown_role_is_an_error_and_not_a_fallback(tmp_path):
    """A typo here hides the profile from the picker or from `summon` silently."""
    write_profile(tmp_path, 'broken', extra='role: helper\n')
    with pytest.raises(AgentParseError, match='`role` must be one of'):
        parse_profile(tmp_path / 'broken.md', source='project')


def test_the_registry_keeps_the_two_roles_apart(tmp_path):
    write_profile(tmp_path, 'wizard')
    write_profile(tmp_path, 'researcher', extra='role: subagent\n')
    write_profile(tmp_path, 'reviewer', extra='role: subagent\n')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='wizard')

    assert [profile.name for profile in registry.agents()] == ['wizard']
    assert [profile.name for profile in registry.subagents()] == ['researcher', 'reviewer']
    # `list()` is everything: it is what the summon list is drawn from.
    assert len(registry.list()) == 3


def test_a_subagent_cannot_be_the_agent_of_the_session(tmp_path):
    write_profile(tmp_path, 'wizard')
    write_profile(tmp_path, 'researcher', extra='role: subagent\n')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='wizard')

    with pytest.raises(AgentNotFound, match='is a subagent'):
        registry.activate('researcher')


def test_a_remembered_subagent_falls_back_to_an_agent(tmp_path):
    """A session saved under a subagent name must still open with someone."""
    write_profile(tmp_path, 'wizard')
    write_profile(tmp_path, 'researcher', extra='role: subagent\n')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='researcher')

    assert registry.active_name == 'wizard'


def test_an_agent_cannot_be_summoned(tmp_path):
    write_profile(tmp_path, 'wizard')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')])

    with pytest.raises(AgentNotFound, match='is an agent, not a subagent'):
        registry.require_subagent('wizard')


# --- the block that lists them -----------------------------------------------


def _registry_with(tmp_path: Path, *, permissions: str = '') -> AgentRegistry:
    write_profile(tmp_path, 'wizard', extra=permissions)
    write_profile(tmp_path, 'researcher', extra='role: subagent\n', description='Reads a tree.')
    write_profile(tmp_path, 'reviewer', extra='role: subagent\n')
    write_profile(tmp_path, 'secret', extra='role: subagent\n')
    return AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='wizard')


def test_without_a_summon_rule_every_subagent_is_listed(tmp_path):
    registry = _registry_with(tmp_path)
    names = [profile.name for profile in summonable(registry, registry.active)]
    assert names == ['researcher', 'reviewer', 'secret']


def test_an_allow_list_narrows_what_is_listed_and_what_is_allowed(tmp_path):
    permissions = 'permissions:\n  summon:\n    allow: ["researcher", "review*"]\n'
    registry = _registry_with(tmp_path, permissions=permissions)

    names = [profile.name for profile in summonable(registry, registry.active)]
    assert names == ['researcher', 'reviewer']


def test_the_block_names_the_subagents_and_how_to_call_them(tmp_path):
    registry = _registry_with(tmp_path)
    block = render_subagents(registry, registry.active)
    assert '## Subagents you can summon' in block
    assert '`researcher`' in block
    assert 'Reads a tree.' in block
    assert 'mode="async"' in block


def test_a_closed_summon_rule_produces_no_block(tmp_path):
    permissions = 'permissions:\n  summon: deny\n'
    registry = _registry_with(tmp_path, permissions=permissions)
    assert render_subagents(registry, registry.active) == ''
    assert summonable(registry, registry.active) == []


def test_a_subagent_profile_is_a_different_block(tmp_path):
    write_profile(tmp_path, 'researcher', extra='role: subagent\n', body='You dig.')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='researcher')

    agent = create_agent(None, agent_registry=registry, profile=ProfileSource.fixed(registry.require('researcher')))

    with agent.override(model=_model(lambda messages, info: ModelResponse(parts=[TextPart('ok')]))):
        result = asyncio.run(agent.run('hello', deps=SigilDeps(node_id='n1')))

    instructions = result.all_messages()[0].instructions or ''
    assert '## Subagent: Researcher' in instructions
    assert 'You dig.' in instructions
    assert 'You were summoned by another agent' in instructions


# --- the tree ----------------------------------------------------------------


def test_the_tree_mints_one_root_and_keeps_its_name_in_step():
    tree = AgentTree()
    root = tree.ensure_root('wizard')
    assert root.is_root and root.depth == 0 and root.role is AgentRole.AGENT
    assert tree.ensure_root('wizard') is root
    assert tree.ensure_root('reviewer').name == 'reviewer'
    assert len(tree) == 1


def test_a_child_hangs_where_its_parent_is_and_not_at_a_fixed_depth():
    tree = AgentTree()
    root = tree.ensure_root('wizard')
    first = tree.add(parent_id=root.id, name='researcher', role=AgentRole.SUBAGENT, task='a')
    second = tree.add(parent_id=first.id, name='reviewer', role=AgentRole.SUBAGENT, task='b')

    assert first.depth == 1
    assert second.depth == 2
    assert [node.name for node in tree.path(second.id)] == ['wizard', 'researcher', 'reviewer']


def test_the_nodes_are_read_depth_first():
    tree = AgentTree()
    root = tree.ensure_root('wizard')
    first = tree.add(parent_id=root.id, name='a', role=AgentRole.SUBAGENT)
    tree.add(parent_id=first.id, name='a-child', role=AgentRole.SUBAGENT)
    tree.add(parent_id=root.id, name='b', role=AgentRole.SUBAGENT)

    assert [node.name for node in tree.nodes()] == ['wizard', 'a', 'a-child', 'b']


def test_a_node_remembers_how_it_ended():
    node = AgentNode(id='1', name='x', role=AgentRole.SUBAGENT, parent_id='0', depth=1)
    assert node.status is NodeStatus.RUNNING
    node.finish('done!')
    assert node.status is NodeStatus.DONE and node.result == 'done!' and node.finished_at is not None
    assert node.elapsed >= 0


def test_a_tree_survives_a_round_trip_through_json():
    tree = AgentTree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='researcher', role=AgentRole.SUBAGENT, task='read', mode='async')
    node.finish('I read it.')

    restored = AgentTree.from_dict(_as_json(tree.to_dict()))
    assert restored is not None
    assert [n.name for n in restored.nodes()] == ['wizard', 'researcher']
    assert restored.root_id == root.id
    assert restored.node(node.id).result == 'I read it.'
    assert restored.node(node.id).mode == 'async'


def test_a_node_without_a_parent_that_cannot_be_read_is_dropped():
    payload = {'nodes': [{'nope': True}, {'id': 'a', 'name': 'x', 'parent_id': None, 'depth': 0}]}
    restored = AgentTree.from_dict(payload)
    assert restored is not None
    assert [node.name for node in restored.nodes()] == ['x']


def test_a_tree_that_cannot_be_read_is_none():
    assert AgentTree.from_dict(None) is None
    assert AgentTree.from_dict({'nodes': 'nope'}) is None
    assert AgentTree.from_dict({'nodes': []}) is None


def test_the_tree_travels_with_the_session(tmp_path):
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
    session = store.create_session()
    trees = AgentTreeStore()
    tree = trees.bind(session, root_name='wizard')
    node = tree.add(parent_id=tree.root_id, name='researcher', role=AgentRole.SUBAGENT, task='read')
    node.finish('read it')
    store.save(session)

    loaded = store.load(session.id)
    assert loaded is not None
    assert loaded.tree is not None
    assert [n.name for n in loaded.tree.nodes()] == ['wizard', 'researcher']
    assert loaded.tree.node(node.id).result == 'read it'


def test_a_session_with_no_tree_gets_a_root_when_it_is_bound(tmp_path):
    """A conversation always has a root, minted the first time it is bound.

    The id is derived from the session, so the tree and the database name the same
    root from the first write.
    """
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions')
    session = store.create_session()
    session.agent = 'wizard'

    assert session.tree is None
    tree = AgentTreeStore().bind(session, root_name='wizard')

    assert tree.root is not None and tree.root.name == 'wizard'
    assert tree.root.id == session.root_id


# --- summoning ---------------------------------------------------------------


def _model(handler: Callable[[list[ModelMessage], AgentInfo], ModelResponse]) -> FunctionModel:
    return FunctionModel(handler)


def _as_json(data: Any) -> Any:
    """Round trip through the JSON the session file uses."""
    import json

    return json.loads(json.dumps(data))


def _texts(messages: list[ModelMessage]) -> list[str]:
    """The user prompts of a history, as the model reads them."""
    found: list[str] = []
    for message in messages:
        for part in getattr(message, 'parts', ()):
            if getattr(part, 'part_kind', '') == 'user-prompt':
                found.append(str(part.content))
    return found


def _tool_returns(messages: list[ModelMessage]) -> list[Any]:
    return [
        part
        for message in messages
        for part in getattr(message, 'parts', ())
        if getattr(part, 'part_kind', '') == 'tool-return'
    ]


@dataclass
class Env:
    """A session, its tree, the summoner and the agents that talk to it."""

    tmp: Path
    registry: AgentRegistry
    trees: AgentTreeStore
    summoner: SubagentSummoner
    factory: AgentFactory
    seen: list[str] = field(default_factory=list)

    def agent(self) -> Agent[Any, Any]:
        return self.factory.root()

    def tree(self) -> AgentTree:
        return self.trees.active


def make_env(tmp_path: Path, *, permissions: str = '', root_answer: str = 'The worker says 42.') -> Env:
    """A root profile that may summon `worker`, and a worker that answers."""
    write_profile(tmp_path, 'wizard', extra=permissions)
    write_profile(tmp_path, 'worker', extra='role: subagent\n', body='You are a worker.')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='wizard')
    trees = AgentTreeStore()
    env = Env(tmp=tmp_path, registry=registry, trees=trees, summoner=None, factory=None)  # type: ignore[arg-type]
    seen = env.seen

    def worker_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append('worker')
        return ModelResponse(parts=[TextPart(root_answer)])

    def build(profile):
        if profile is None:
            raise AssertionError('the factory is only asked for subagents here')
        subagent = create_agent(
            None,
            model=_model(worker_model),
            agent_registry=registry,
            profile=ProfileSource.fixed(profile),
        )
        # Every agent of the session gets `summon`, the subagents included: that is
        # what makes a subagent able to summon another one.
        register_tree_tools(subagent, env.summoner)
        return subagent

    def install(target):
        register_tree_tools(target, env.summoner)

    def root_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append('root')
        if not _tool_returns(messages):
            return ModelResponse(parts=[ToolCallPart('summon', {'agent': 'worker', 'task': 'compute it'})])
        return ModelResponse(parts=[TextPart('done')])

    env.summoner = SubagentSummoner(
        trees=trees,
        registry=registry,
        builder=lambda: build,
        max_steps=20,
    )
    env.factory = AgentFactory(
        AgentSpec(
            model=_model(root_model),
            agent_registry=registry,
            tool_registrars=[install],
        )
    )
    session = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions').create_session()
    trees.bind(session, root_name='wizard')
    return env


def test_a_sync_summon_returns_the_message_as_the_tool_result(tmp_path):
    env = make_env(tmp_path)

    async def inner():
        agent = env.agent()
        async with agent.iter('go', deps=SigilDeps()) as run:
            async for _node in run:
                pass
        return run.all_messages()

    messages = asyncio.run(inner())
    returns = _tool_returns(messages)
    node = env.tree().nodes()[1]

    assert len(returns) == 1
    # The result is the subagent's message under a header that names the
    # execution: the id is what the parent needs to message, kill or resume it.
    assert 'The worker says 42.' in str(returns[0].content)
    assert f'#{node.short_id}' in str(returns[0].content)
    assert returns[0].outcome == 'success'


def test_the_node_records_what_it_ran_and_what_it_answered(tmp_path):
    env = make_env(tmp_path)

    async def inner():
        agent = env.agent()
        async with agent.iter('go', deps=SigilDeps()) as run:
            async for _node in run:
                pass

    asyncio.run(inner())
    nodes = env.tree().nodes()
    worker = nodes[1]

    assert worker.name == 'worker'
    assert worker.depth == 1
    assert worker.status is NodeStatus.DONE
    assert worker.task == 'compute it'
    assert worker.result == 'The worker says 42.'
    # Its own transcript, not the parent's: that is what the UI opens.
    assert _texts(worker.messages) == ['compute it']


def test_a_subagent_runs_its_own_profile_and_not_the_active_one(tmp_path):
    """The worker's prompt is its own: `create_agent` is asked for that profile."""
    env = make_env(tmp_path)

    async def inner():
        agent = env.agent()
        async with agent.iter('go', deps=SigilDeps()) as run:
            async for _node in run:
                pass

    asyncio.run(inner())
    worker = env.tree().nodes()[1]
    instructions = worker.messages[0].instructions or ''
    assert '## Subagent: Worker' in instructions
    assert 'You are a worker.' in instructions


def test_a_name_the_permission_does_not_allow_is_refused_as_a_failed_call(tmp_path):
    env = make_env(tmp_path, permissions='permissions:\n  summon:\n    allow: ["someone-else"]\n')

    async def inner():
        agent = env.agent()
        async with agent.iter('go', deps=SigilDeps()) as run:
            async for _node in run:
                pass
        return run.all_messages()

    returns = _tool_returns(asyncio.run(inner()))
    assert returns[0].outcome == 'failed'
    assert 'PermissionDenied' in str(returns[0].content)
    # Nothing was created: a refused call does not leave a node behind.
    assert len(env.tree()) == 1


def test_an_unknown_subagent_names_the_ones_that_exist(tmp_path):
    env = make_env(tmp_path)

    async def inner():
        return await env.summoner.summon(parent_id=None, name='nobody', task='x')

    with pytest.raises(SummonError, match='Available subagents: worker'):
        asyncio.run(inner())


def test_an_agent_profile_cannot_be_summoned(tmp_path):
    env = make_env(tmp_path)

    with pytest.raises(SummonError, match='is an agent, not a subagent'):
        asyncio.run(env.summoner.summon(parent_id=None, name='wizard', task='x'))


def test_a_summon_without_a_task_is_refused(tmp_path):
    env = make_env(tmp_path)

    with pytest.raises(SummonError, match='`task` is empty'):
        asyncio.run(env.summoner.summon(parent_id=None, name='worker', task='   '))


def test_the_chain_of_subagents_is_capped(tmp_path):
    env = make_env(tmp_path)
    env.summoner = SubagentSummoner(
        trees=env.trees,
        registry=env.registry,
        builder=lambda: lambda profile: Agent(_model(lambda m, i: ModelResponse(parts=[TextPart('x')]))),
        max_depth=1,
    )

    async def inner():
        tree = env.tree()
        # Pretend the root already summoned `worker`, and that worker tries to
        # summon another profile: one more level than the cap allows.
        write_profile(env.tmp, 'deeper', extra='role: subagent\n')
        env.registry.refresh()
        first = tree.add(parent_id=tree.root_id, name='worker', role=AgentRole.SUBAGENT, task='a')
        return await env.summoner.summon(parent_id=first.id, name='deeper', task='b')

    with pytest.raises(SummonError, match='nested too deep'):
        asyncio.run(inner())


def test_a_subagent_cannot_summon_itself(tmp_path):
    """The permissions would allow it; the loop it opens is what stops it."""
    env = make_env(tmp_path)

    async def inner():
        tree = env.tree()
        mine = tree.add(parent_id=tree.root_id, name='worker', role=AgentRole.SUBAGENT, task='a')
        return await env.summoner.summon(parent_id=mine.id, name='worker', task='b')

    with pytest.raises(SummonError, match='cannot summon itself'):
        asyncio.run(inner())


def test_a_failing_subagent_tells_its_parent_why(tmp_path):
    env = make_env(tmp_path)

    def broken(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError('the provider exploded')

    env.summoner = SubagentSummoner(
        trees=env.trees,
        registry=env.registry,
        builder=lambda: lambda profile: create_agent(None, model=_model(broken), agent_registry=env.registry),
    )

    async def inner():
        agent = env.agent()
        async with agent.iter('go', deps=SigilDeps()) as run:
            async for _node in run:
                pass
        return run.all_messages()

    returns = _tool_returns(asyncio.run(inner()))
    node = env.tree().nodes()[1]
    assert node.status is NodeStatus.FAILED
    assert 'provider exploded' in (node.error or '')
    assert returns[0].outcome == 'failed'
    assert 'provider exploded' in str(returns[0].content)


# --- async -------------------------------------------------------------------


def test_an_async_answer_that_arrives_with_nobody_running_waits_for_the_next_turn(tmp_path):
    env = make_env(tmp_path)
    taken: list[tuple[Any, str]] = []

    env.summoner = SubagentSummoner(
        trees=env.trees,
        registry=env.registry,
        builder=lambda: (
            lambda profile: create_agent(
                None,
                model=_model(lambda m, i: ModelResponse(parts=[TextPart('late news')])),
                agent_registry=env.registry,
                profile=ProfileSource.fixed(profile),
            )
        ),
        on_delivered=lambda node: taken.append(node),
    )

    async def inner():
        # No root run at all: the answer must not be lost and must not start a turn.
        await env.summoner.summon(parent_id=None, name='worker', task='go', mode='async')
        for _ in range(50):
            await asyncio.sleep(0.01)
            if env.tree().nodes()[1].status.finished:
                break

    asyncio.run(inner())

    # Nobody was running, so the notice never fired and the message waited.
    assert not taken
    pending = env.summoner.drain_pending()
    assert pending and 'late news' in pending[0]
    assert env.summoner.drain_pending() == []


def test_a_cancelled_async_subagent_sends_nothing(tmp_path):
    env = make_env(tmp_path)
    taken: list[Any] = []

    def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart('never read')])

    env.summoner = SubagentSummoner(
        trees=env.trees,
        registry=env.registry,
        builder=lambda: lambda profile: create_agent(None, model=_model(slow), agent_registry=env.registry),
        on_delivered=lambda node: taken.append(node),
    )

    async def inner():
        await env.summoner.summon(parent_id=None, name='worker', task='go', mode='async')
        env.summoner.cancel_all()
        for _ in range(50):
            await asyncio.sleep(0.01)
            if env.tree().nodes()[1].status.finished:
                return

    asyncio.run(inner())
    node = env.tree().nodes()[1]
    # The cancellation is recorded even when the task never got to run.
    assert node.status is NodeStatus.CANCELLED
    assert not taken
    assert env.summoner.drain_pending() == []


def test_a_subagent_of_a_subagent_climbs_to_the_root(tmp_path):
    """Nesting is allowed, and a grandfather still gets the message."""
    env = make_env(tmp_path)

    def middle_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not _tool_returns(messages):
            return ModelResponse(parts=[ToolCallPart('summon', {'agent': 'worker', 'task': 'deep work'})])
        return ModelResponse(parts=[TextPart('middle read it')])

    write_profile(env.tmp, 'middle', extra='role: subagent\n')
    env.registry.refresh()

    def build(profile):
        # The middle subagent gets `summon` too: it is an agent like any other, and
        # nesting is the same tool one level down.
        subagent = create_agent(
            None,
            model=_model(
                middle_model
                if profile.name == 'middle'
                else lambda m, i: ModelResponse(parts=[TextPart('deep answer')])
            ),
            agent_registry=env.registry,
            profile=ProfileSource.fixed(profile),
        )
        register_tree_tools(subagent, env.summoner)
        return subagent

    env.summoner = SubagentSummoner(
        trees=env.trees,
        registry=env.registry,
        builder=lambda: build,
        max_steps=20,
        max_depth=3,
    )

    async def inner():
        await env.summoner.summon(parent_id=None, name='middle', task='go', mode='async')
        for _ in range(100):
            await asyncio.sleep(0.01)
            if len(env.tree()) >= 3 and env.tree().nodes()[2].status.finished:
                return

    asyncio.run(inner())
    nodes = env.tree().nodes()
    assert [node.depth for node in nodes] == [0, 1, 2]
    assert nodes[2].name == 'worker'
    assert nodes[2].result == 'deep answer'


def _advertised(tmp_path: Path, *, permissions: str) -> set[str]:
    """The tools the model actually sees on a request, for a profile."""
    write_profile(tmp_path, 'wizard', extra=permissions)
    write_profile(tmp_path, 'worker', extra='role: subagent\n')
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='wizard')
    summoner = SubagentSummoner(trees=AgentTreeStore(), registry=registry, builder=lambda: lambda profile: None)
    seen: set[str] = set()

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.update(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart('ok')])

    agent = create_agent(None, model=_model(model), agent_registry=registry)
    register_tree_tools(agent, summoner)
    asyncio.run(agent.run('hello', deps=SigilDeps()))
    return seen


def test_a_profile_that_may_not_summon_does_not_advertise_the_tool(tmp_path):
    """A whole-tool denial hides it: the model cannot plan around a refusal."""
    assert 'summon' not in _advertised(tmp_path, permissions='permissions:\n  summon: deny\n')


def test_a_profile_that_may_summon_does_advertise_the_tool(tmp_path):
    assert 'summon' in _advertised(tmp_path, permissions='')


def test_an_async_subagent_keeps_the_turn_alive_until_it_answers(tmp_path):
    """The parent's run stays open while the node works, and the answer lands inside."""
    env = make_env(tmp_path)
    prompts: list[list[str]] = []
    # The run does not exist when the summoner is built — it is created by the
    # `async with` below — so the app's provider is a closure over a holder. That is
    # exactly how `TreeMixin.root_run` reads `_current_run` in the real app.
    holder: list[Any] = [None]
    notices: list[AgentNode] = []

    def root_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        prompts.append(_texts(messages))
        if any('subagent Worker' in text for text in _texts(messages)):
            return ModelResponse(parts=[TextPart('read it')])
        if not _tool_returns(messages):
            return ModelResponse(
                parts=[ToolCallPart('summon', {'agent': 'worker', 'task': 'compute it', 'mode': 'async'})]
            )
        return ModelResponse(parts=[ToolCallPart('hold', {})])

    env.summoner = SubagentSummoner(
        trees=env.trees,
        registry=env.registry,
        builder=lambda: (
            lambda profile: create_agent(
                None,
                model=_model(lambda m, i: ModelResponse(parts=[TextPart('The worker says 42.')])),
                agent_registry=env.registry,
                profile=ProfileSource.fixed(profile),
            )
        ),
        max_steps=20,
        root_run=lambda: holder[0],
        on_delivered=notices.append,
    )
    agent = _agent_with_hold(env, root_model)

    async def inner():
        async with agent.iter('go', deps=SigilDeps(), usage_limits=UsageLimits(request_limit=None)) as run:
            holder[0] = run
            async for _node in run:
                pass

    asyncio.run(inner())

    # The last request the model saw carried the subagent's message as a prompt.
    assert any('subagent Worker' in text for text in prompts[-1])
    assert notices and notices[0].name == 'worker'
    # Delivered inside the turn, so nothing is left for the next one.
    assert env.summoner.drain_pending() == []


def _agent_with_hold(env: Env, root_model) -> Agent[Any, Any]:
    """The root agent of ``env``, plus a tool that keeps its run alive.

    The tool waits for the tree, not for a clock: `hold` returns as soon as the
    async node it is waiting on has finished, which is exactly the moment the
    message is delivered, and it never waits longer than a bounded number of
    ticks so a broken delivery fails the test instead of hanging it.
    """

    def install(target):
        register_tree_tools(target, env.summoner)

        @target.tool_plain(name='hold')
        async def hold() -> str:
            for _ in range(300):
                await asyncio.sleep(0.005)
                nodes = env.tree().nodes()
                if len(nodes) > 1 and nodes[1].status.finished:
                    return 'held'
            return 'tired of holding'

    return AgentFactory(
        AgentSpec(model=_model(root_model), agent_registry=env.registry, tool_registrars=[install])
    ).root()


def test_restricted_profile_resumes_allowed_worker_by_id_through_agent(tmp_path):
    env = make_env(tmp_path, permissions='permissions:\n  summon:\n    allow: ["worker"]\n')
    root = env.tree().root
    node = env.tree().add(parent_id=root.id, name='worker', role=AgentRole.SUBAGENT, task='first')
    node.finish('first result')

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not _tool_returns(messages):
            return ModelResponse(parts=[ToolCallPart('summon', {'agent_id': node.short_id, 'task': 'continue'})])
        return ModelResponse(parts=[TextPart('done')])

    async def inner():
        async with env.agent().iter('go', deps=SigilDeps(), model=_model(model)) as run:
            async for _ in run:
                pass
        return run.all_messages()

    returns = _tool_returns(asyncio.run(inner()))
    assert returns[0].outcome == 'success'
    assert 'The worker says 42.' in str(returns[0].content)
