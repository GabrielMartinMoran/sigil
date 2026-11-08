"""The second wave of the tree: budgets, resuming, stopping, backgrounding, talking.

Five behaviours, and each one is a question a user asks of a tree of agents:

- **A subagent runs out of steps.** Does it say what it got, and can the parent pick
  it up again from there instead of starting over?
- **An id.** Is a second call with an id a continuation of the same conversation,
  and a second call without one a new node?
- **Killing.** Does stopping a node stop what it summoned, and does it leave the
  caller's turn alive?
- **The background.** Can a call that is already blocking be moved out of the way?
- **The channel.** Does a child get a voice only when its profile asks for one, and
  do siblings hear each other only when named?

The environment is the real thing: a real `Agent.iter` for the parent, a real
summoner, and a tree that is written and read like a session's.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import UsageLimits

from src.engine.agents.agent import create_agent
from src.engine.agents.factory import AgentFactory, AgentSpec
from src.engine.agents.profile.registry import AgentRegistry
from src.engine.agents.profile.source import ProfileSource
from src.engine.agents.step_budget import PREFIX
from src.engine.agents.tree.models.node_status import NodeStatus
from src.engine.agents.tree.models.summon_error import SummonError
from src.engine.agents.tree.rendering import render_peers
from src.engine.agents.tree.store import AgentTreeStore
from src.engine.agents.tree.summoner import SigilDeps, SubagentSummoner
from src.engine.agents.tree.tools import register_tree_tools
from src.engine.agents.tree.tree import AgentTree
from src.engine.sessions.session_store import SessionStore

FRONTMATTER = '---\nname: {name}\ndescription: {description}\n{extra}---\n{body}'


def write_profile(root: Path, name: str, *, extra: str = '', body: str = 'Be brief.') -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f'{name}.md'
    path.write_text(
        FRONTMATTER.format(name=name, description=f'Profile {name}.', extra=extra, body=body),
        encoding='utf-8',
    )
    return path


def _model(handler: Callable[[list[ModelMessage], AgentInfo], ModelResponse]) -> FunctionModel:
    return FunctionModel(handler)


def _texts(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        for part in getattr(message, 'parts', ())
        if getattr(part, 'part_kind', '') == 'user-prompt'
    ]


def _tool_returns(messages: list[ModelMessage]) -> list[Any]:
    return [
        part
        for message in messages
        for part in getattr(message, 'parts', ())
        if getattr(part, 'part_kind', '') == 'tool-return'
    ]


@dataclass
class Env:
    """A session, its tree, the summoner, and one model per profile."""

    tmp: Path
    registry: AgentRegistry
    trees: AgentTreeStore
    summoner: SubagentSummoner
    factory: AgentFactory
    scripts: dict[str, Callable[[list[ModelMessage], AgentInfo], ModelResponse]] = field(default_factory=dict)

    def tree(self) -> AgentTree:
        return self.trees.active


def make_env(
    tmp_path: Path,
    *,
    root: Callable[[list[ModelMessage], AgentInfo], ModelResponse],
    profiles: dict[str, dict[str, Any]] | None = None,
    scripts: dict[str, Callable[[list[ModelMessage], AgentInfo], ModelResponse]] | None = None,
    max_steps: int = 0,
    max_depth: int = 3,
    root_run: Callable[[], Any | None] | None = None,
) -> Env:
    """Builds the session: ``profiles`` is name -> (frontmatter extra, model)."""
    write_profile(tmp_path, 'wizard')
    registry_profiles: dict[str, dict[str, Any]] = dict(profiles or {})
    for name, spec in registry_profiles.items():
        write_profile(tmp_path, name, extra=spec.get('extra', 'role: subagent\n'), body=spec.get('body', 'You work.'))
    registry = AgentRegistry([('project', tmp_path), ('builtin', tmp_path / 'none')], active='wizard')
    trees = AgentTreeStore()
    env = Env(tmp=tmp_path, registry=registry, trees=trees, summoner=None, factory=None)  # type: ignore[arg-type]
    models: dict[str, FunctionModel] = {'wizard': _model(root)}
    for name, script in (scripts or {}).items():
        models[name] = _model(script)
    env.scripts = scripts or {}

    def build(profile):
        agent = create_agent(
            None,
            model=models[profile.name],
            agent_registry=registry,
            profile=ProfileSource.fixed(profile),
        )
        register_tree_tools(agent, env.summoner)

        # A tool with a cost, so a scripted subagent can spend a step on something
        # real. It is the cheapest way to exercise a budget end to end.
        @agent.tool_plain(name='ping')
        def ping() -> str:
            return 'pong'

        return agent

    def install(target):
        register_tree_tools(target, env.summoner)

    env.summoner = SubagentSummoner(
        trees=trees,
        registry=registry,
        builder=lambda: build,
        max_steps=max_steps,
        max_depth=max_depth,
        root_run=root_run,
    )
    env.factory = AgentFactory(AgentSpec(model=_model(root), agent_registry=registry, tool_registrars=[install]))
    session = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'sessions').create_session()
    trees.bind(session, root_name='wizard')
    return env


def run_root(env: Env, prompt: str = 'go') -> list[ModelMessage]:
    """Runs the root turn and returns its history."""

    async def inner():
        agent = env.factory.root()
        async with agent.iter(prompt, deps=SigilDeps(), usage_limits=UsageLimits(request_limit=None)) as run:
            async for _node in run:
                pass
        return run.all_messages()

    return asyncio.run(inner())


# --- max_steps ---------------------------------------------------------------


def test_a_subagent_that_runs_out_of_steps_still_reports(tmp_path):
    """The report is partial, and the header says so — that is what makes it usable."""
    calls = {'n': 0}

    def busy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls['n'] += 1
        if info.function_tools:
            return ModelResponse(parts=[ToolCallPart('ping', {})])
        return ModelResponse(parts=[TextPart('I read half the tree.')])

    env = make_env(
        tmp_path,
        root=_root_calling('summon', {'agent': 'worker', 'task': 'read it'}),
        profiles={'worker': {'extra': 'role: subagent\nmax_steps: 2\n'}},
        scripts={'worker': busy},
    )

    def install(target):
        register_tree_tools(target, env.summoner)

    env.factory = AgentFactory(
        AgentSpec(
            model=_model(_root_calling('summon', {'agent': 'worker', 'task': 'go'})),
            agent_registry=env.registry,
            tool_registrars=[install],
        )
    )

    messages = run_root(env)
    node = env.tree().nodes()[1]
    result = str(_tool_returns(messages)[0].content)

    assert node.status is NodeStatus.DONE
    assert node.budget_exhausted
    assert 'I read half the tree.' in result
    assert 'out of steps' in result
    # The notice is in the subagent's transcript, so the view explains it too.
    assert any(PREFIX in text for text in _texts(node.messages))


def test_an_exhausted_subagent_can_be_resumed_with_its_id(tmp_path):
    """The second call continues the conversation: nothing it read is lost."""
    state = {'n': 0}

    def worker(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        state['n'] += 1
        if state['n'] == 1 and info.function_tools:
            return ModelResponse(parts=[ToolCallPart('ping', {})])
        if state['n'] == 2:
            return ModelResponse(parts=[TextPart('first half done')])
        # The resumed run sees the whole previous conversation.
        seen = _texts(messages)
        assert any('first half done' in text for text in seen) or len(seen) >= 2
        return ModelResponse(parts=[TextPart('second half done')])

    env = make_env(
        tmp_path,
        root=_root_with_script([('summon', {'agent': 'worker', 'task': 'read it'}), ('summon_resume', None)]),
        profiles={'worker': {'extra': 'role: subagent\nmax_steps: 1\n'}},
        scripts={'worker': worker},
    )

    async def inner():
        first = await env.summoner.summon(parent_id=None, name='worker', task='read the first half')
        node = env.tree().node(first.split('#')[1][:8]) if '#' in first else env.tree().nodes()[1]
        node = node or env.tree().nodes()[1]
        assert node.status is NodeStatus.DONE
        second = await env.summoner.summon(parent_id=None, task='read the second half', agent_id=node.id)
        return node, second

    node, second = asyncio.run(inner())
    assert 'second half done' in second
    assert node.runs == 2
    # The transcript grew instead of being replaced: the first run's messages are
    # still there, and the second prompt sits after them.
    assert len(node.messages) >= 4
    assert any('read the second half' in text for text in _texts(node.messages))


def test_a_resume_of_an_unknown_id_explains_what_the_id_is(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}))

    with pytest.raises(SummonError, match='not the name of the profile'):
        asyncio.run(env.summoner.summon(parent_id=None, task='go', agent_id='nope'))


def test_a_running_node_cannot_be_resumed(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='go')

    with pytest.raises(SummonError, match='still working'):
        asyncio.run(env.summoner.summon(parent_id=None, task='more', agent_id=node.id))


def test_two_summons_without_an_id_are_two_nodes(tmp_path):
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'worker': {}},
        scripts={'worker': lambda m, i: ModelResponse(parts=[TextPart('done')])},
    )

    async def inner():
        await env.summoner.summon(parent_id=None, name='worker', task='a')
        await env.summoner.summon(parent_id=None, name='worker', task='b')

    asyncio.run(inner())
    nodes = env.tree().nodes()
    assert [node.name for node in nodes] == ['wizard', 'worker', 'worker']
    assert nodes[1].id != nodes[2].id


# --- killing -----------------------------------------------------------------


def test_killing_a_node_stops_it_and_everything_under_it(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}, 'deeper': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    parent = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    child = tree.add(parent_id=parent.id, name='deeper', role=node_role(), task='b')

    message = env.summoner.kill(None, parent.id)

    assert 'stopped' in message
    assert parent.status is NodeStatus.CANCELLED
    assert child.status is NodeStatus.CANCELLED


def test_killing_a_finished_node_is_a_no_op(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    node.finish('done')

    assert 'had already finished' in env.summoner.kill(None, node.id)


def test_a_node_cannot_kill_its_own_parent(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}, 'deeper': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    parent = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    child = tree.add(parent_id=parent.id, name='deeper', role=node_role(), task='b')

    with pytest.raises(SummonError, match='cannot stop it'):
        env.summoner.kill(child.id, parent.id)


def test_the_root_cannot_be_killed(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}))
    root = env.tree().ensure_root('wizard')

    with pytest.raises(SummonError, match='stop the turn instead'):
        env.summoner.kill(None, root.id)


def test_a_killed_sync_summon_does_not_cancel_the_callers_turn(tmp_path):
    """Killing a subagent from the UI must not take the parent's turn with it."""
    env = make_env(
        tmp_path,
        root=_root_calling('summon', {'agent': 'worker', 'task': 'slow'}),
        profiles={'worker': {}},
        scripts={'worker': lambda m, i: ModelResponse(parts=[TextPart('never')])},
    )

    async def inner():
        task = asyncio.ensure_future(env.summoner.summon(parent_id=None, name='worker', task='slow'))
        for _ in range(100):
            await asyncio.sleep(0.005)
            if len(env.tree()) > 1:
                break
        node = env.tree().nodes()[1]
        env.summoner.kill(None, node.id)
        result = await asyncio.wait_for(task, timeout=5)
        return result, node

    result, node = asyncio.run(inner())
    assert node.status is NodeStatus.CANCELLED
    assert 'stopped before it answered' in result


# --- the background ----------------------------------------------------------


def test_a_blocking_call_can_be_moved_to_the_background(tmp_path):
    """The caller stops waiting; the node finishes and its answer arrives later."""
    env = make_env(
        tmp_path,
        root=_root_calling('summon', {'agent': 'worker', 'task': 'slow'}),
        profiles={'worker': {}},
        scripts={'worker': lambda m, i: ModelResponse(parts=[TextPart('arrived late')])},
    )

    async def inner():
        task = asyncio.ensure_future(env.summoner.summon(parent_id=None, name='worker', task='slow', mode='sync'))
        for _ in range(100):
            await asyncio.sleep(0.005)
            if len(env.tree()) > 1:
                break
        node = env.tree().nodes()[1]
        message = env.summoner.detach(node.id)
        result = await asyncio.wait_for(task, timeout=5)
        for _ in range(200):
            await asyncio.sleep(0.005)
            if node.status.finished:
                break
        return node, message, result

    node, message, result = asyncio.run(inner())
    assert 'moved to the background' in message
    assert node.mode == 'async'
    # The caller got the acknowledgement, not the answer: the answer went its way.
    assert 'moved to the background' in result
    assert node.status is NodeStatus.DONE
    # Nobody was inside a turn, so the answer waits for the next one rather than
    # being lost: that is what makes moving a call to the background safe.
    waiting = env.summoner.take_messages()
    assert waiting and 'arrived late' in waiting[0]


def test_a_finished_node_cannot_be_backgrounded(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    node.finish('done')

    with pytest.raises(SummonError, match='already finished'):
        env.summoner.detach(node.id)


# --- the channel -------------------------------------------------------------


def test_a_unidirectional_child_cannot_talk_back(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')

    with pytest.raises(SummonError, match='does not allow talking to your parent'):
        env.summoner.send_message(sender_id=node.id, target_id=root.id, text='hi')


def test_a_bidirectional_child_reaches_its_parent_when_it_is_working(tmp_path):
    """The channel decides *whether*; the live turn decides *now*."""
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'worker': {'extra': 'role: subagent\nchannel: bidirectional\n'}},
    )
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    node.channel = env.registry.require('worker').channel
    run = _LiveRun()
    env.summoner.listen(root_run=lambda: run)

    message = env.summoner.send_message(sender_id=node.id, target_id=root.id, text='found something')

    assert 'delivered' in message
    assert run.enqueued == []
    assert tree.notifications.for_receiver(root.id)[0].text == 'found something'


def test_a_parent_steers_a_running_child_with_an_instruction(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}, 'deeper': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')

    class Run:
        def __init__(self) -> None:
            self.enqueued: list[str] = []

        def enqueue(self, text: str) -> str:
            self.enqueued.append(text)
            return 'id'

    run = Run()
    node.run = run

    message = env.summoner.send_message(sender_id=root.id, target_id=node.id, text='go left instead')

    assert 'queued' in message
    assert 'go left instead' in run.enqueued[0]
    assert 'instruction from your parent' in run.enqueued[0]


def test_a_parent_cannot_message_a_child_that_is_not_running(tmp_path):
    """Nothing waits for a dead node: the parent is told which call brings it back."""
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    node.finish('done')

    with pytest.raises(SummonError) as excinfo:
        env.summoner.send_message(sender_id=root.id, target_id=node.id, text='one more thing')

    message = str(excinfo.value)
    assert 'is not running' in message
    # The parent owns the execution, so the refusal names the exact call that
    # resumes it with this very message.
    assert f'summon(agent_id="{node.id}", task=' in message
    assert 'one more thing' in message
    assert 'keeps its transcript' in message


def test_a_finished_node_keeps_nothing_waiting(tmp_path):
    """A queue nobody promised to drain is a queue that grows in the dark."""
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    node.finish('done')

    with pytest.raises(SummonError):
        env.summoner.send_message(sender_id=root.id, target_id=node.id, text='one more thing')

    assert not hasattr(node, 'inbox')


def test_a_sibling_needs_its_name_in_the_channel(tmp_path):
    siblings = 'channel:\n  mode: bidirectional\n  siblings: [reviewer]\n'
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'reader': {'extra': f'role: subagent\n{siblings}'}, 'reviewer': {}},
    )
    tree = env.tree()
    root = tree.ensure_root('wizard')
    reader = tree.add(parent_id=root.id, name='reader', role=node_role(), task='a')
    reviewer = tree.add(parent_id=root.id, name='reviewer', role=node_role(), task='b')
    reader.channel = env.registry.require('reader').channel
    reviewer.run = _LiveRun()

    message = env.summoner.send_message(sender_id=reader.id, target_id=reviewer.id, text='look at this')

    assert 'delivered' in message
    assert reviewer.run.enqueued == []
    assert tree.notifications.for_receiver(reviewer.id)[0].text == 'look at this'


def test_a_sibling_that_is_not_running_cannot_be_messaged(tmp_path):
    siblings = 'channel:\n  mode: bidirectional\n  siblings: [reviewer]\n'
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'reader': {'extra': f'role: subagent\n{siblings}'}, 'reviewer': {}},
    )
    tree = env.tree()
    root = tree.ensure_root('wizard')
    reader = tree.add(parent_id=root.id, name='reader', role=node_role(), task='a')
    reviewer = tree.add(parent_id=root.id, name='reviewer', role=node_role(), task='b')
    reader.channel = env.registry.require('reader').channel
    reviewer.finish('done')

    with pytest.raises(SummonError, match='already finished'):
        env.summoner.send_message(sender_id=reader.id, target_id=reviewer.id, text='hey')


def test_a_stranger_is_not_reachable(tmp_path):
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'reader': {'extra': 'role: subagent\nchannel: bidirectional\n'}, 'other': {}},
    )
    tree = env.tree()
    root = tree.ensure_root('wizard')
    first = tree.add(parent_id=root.id, name='reader', role=node_role(), task='a')
    middle = tree.add(parent_id=first.id, name='other', role=node_role(), task='b')
    stranger = tree.add(parent_id=middle.id, name='other', role=node_role(), task='c')
    first.channel = env.registry.require('reader').channel

    with pytest.raises(SummonError, match='none of those'):
        env.summoner.send_message(sender_id=first.id, target_id=stranger.id, text='hey')


# --- what the prompt says ----------------------------------------------------


def test_the_peers_block_lists_the_parent_and_the_ids(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')

    block = render_peers(tree, node.id, profile=env.registry.require('worker'), is_live=env.summoner.is_live)

    assert '## The agents around you' in block
    assert f'`{root.short_id}`' in block
    assert '**wizard**' in block
    # The root has no turn running, and the block does not pretend otherwise.
    assert 'idle (not in a turn)' in block


def test_the_peers_block_shows_only_the_siblings_the_channel_names(tmp_path):
    siblings = 'channel:\n  mode: bidirectional\n  siblings: [reviewer]\n'
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'reader': {'extra': f'role: subagent\n{siblings}'}, 'reviewer': {}, 'other': {}},
    )
    tree = env.tree()
    root = tree.ensure_root('wizard')
    reader = tree.add(parent_id=root.id, name='reader', role=node_role(), task='a')
    reviewer = tree.add(parent_id=root.id, name='reviewer', role=node_role(), task='b')
    tree.add(parent_id=root.id, name='other', role=node_role(), task='c')

    block = render_peers(tree, reader.id, profile=env.registry.require('reader'))

    assert f'`{reviewer.short_id}`' in block
    assert '**other**' not in block


def test_the_root_gets_no_peers_block(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}))
    tree = env.tree()
    root = tree.ensure_root('wizard')

    assert render_peers(tree, None) == ''
    assert render_peers(tree, root.id) == ''


def test_a_child_cannot_message_a_parent_that_is_not_in_a_turn(tmp_path):
    """The rule holds in both directions; the advice differs, because the fix does."""
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'worker': {'extra': 'role: subagent\nchannel: bidirectional\n'}},
    )
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    node.channel = env.registry.require('worker').channel

    with pytest.raises(SummonError) as excinfo:
        env.summoner.send_message(sender_id=node.id, target_id=root.id, text='found something')

    message = str(excinfo.value)
    assert 'not in a turn' in message
    # A child cannot summon its parent: the honest advice is the final message.
    assert 'final message' in message
    assert env.summoner.take_messages() == []


def test_answering_a_finished_child_resumes_it_with_the_message(tmp_path):
    """What the refusal asks for, done: the reply becomes the task of the resume."""
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'worker': {}},
        scripts={
            'worker': lambda messages, info: ModelResponse(
                parts=[TextPart('got it' if 'use the short form' in ' '.join(_texts(messages)) else 'first pass')]
            )
        },
    )

    async def inner():
        await env.summoner.summon(parent_id=None, name='worker', task='start')
        node = env.tree().nodes()[1]
        assert node.status.finished
        # The parent answers by re-invoking it with the reply as the task.
        reply = await env.summoner.summon(parent_id=None, task='use the short form', agent_id=node.id)
        return node, reply

    node, reply = asyncio.run(inner())
    assert node.runs == 2
    assert 'got it' in reply
    # The reply was a prompt of the same conversation, on top of the first run.
    assert any('use the short form' in text for text in _texts(node.messages))


# --- what the tools hand the model -------------------------------------------


def test_list_agents_shows_the_ids_and_who_summoned_who(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='read it')
    node.finish('done')

    from src.engine.agents.tree.tools import render_tree_table

    table = render_tree_table(tree, root.id)
    assert 'id | agent | state' in table
    assert node.short_id in table
    assert '(you)' in table
    assert 'read it' in table


def test_list_agents_on_a_session_with_no_subagents_says_so(tmp_path):
    from src.engine.agents.tree.tools import render_tree_table

    tree = AgentTree()
    tree.ensure_root('wizard')
    assert 'no subagents' in render_tree_table(tree, None)


class _LiveRun:
    """A run that only records what was enqueued into it."""

    def __init__(self) -> None:
        self.enqueued: list[str] = []

    def enqueue(self, text: str) -> str:
        self.enqueued.append(text)
        return 'id'


def node_role():
    from src.engine.agents.profile.models.agent_role import AgentRole

    return AgentRole.SUBAGENT


def _root_calling(tool: str, args: dict[str, Any]) -> Callable[[list[ModelMessage], AgentInfo], ModelResponse]:
    """A root model that calls one tool and then answers."""
    calls = {'n': 0}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls['n'] += 1
        if calls['n'] == 1:
            return ModelResponse(parts=[ToolCallPart(tool, args)])
        return ModelResponse(parts=[TextPart('root done')])

    return model


def _root_with_script(steps: list[tuple[str, dict[str, Any] | None]]):
    """A root model that walks a list of tool calls, then answers."""
    state = {'i': 0}

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if state['i'] >= len(steps):
            return ModelResponse(parts=[TextPart('root done')])
        tool, args = steps[state['i']]
        state['i'] += 1
        return ModelResponse(parts=[ToolCallPart(tool, args or {})])

    return model


def test_a_node_that_ends_stops_what_it_had_running_under_it(tmp_path):
    """A branch cannot hang from a parent that stopped: children die with it."""
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'parent': {}, 'child': {}},
        scripts={
            'parent': lambda m, i: ModelResponse(parts=[TextPart('parent done')]),
            'child': lambda m, i: ModelResponse(parts=[TextPart('child done')]),
        },
    )

    async def inner():
        # The child is started first and left running; then the parent finishes.
        await env.summoner.summon(parent_id=None, name='parent', task='go', mode='async')
        for _ in range(200):
            await asyncio.sleep(0.005)
            if env.tree().nodes()[1].status.finished:
                break
        parent = env.tree().nodes()[1]
        child = env.tree().add(parent_id=parent.id, name='child', role=node_role(), task='grandchild')
        # Simulate the parent ending again with a child still running: the cascade
        # is what the finally block of a run does.
        env.summoner._cascade_stop(parent)
        return parent, child

    parent, child = asyncio.run(inner())
    assert parent.status is NodeStatus.DONE
    assert child.status is NodeStatus.CANCELLED


# --- ids as agents actually see them -----------------------------------------


def test_a_short_id_is_enough_to_message_or_resume(tmp_path):
    """The ids agents read are eight characters: the tools must accept them."""
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'worker': {}},
        scripts={'worker': lambda m, i: ModelResponse(parts=[TextPart('done')])},
    )

    async def inner():
        await env.summoner.summon(parent_id=None, name='worker', task='first')
        node = env.tree().nodes()[1]
        # What `list_agents` and the peers block show, and what a human copies.
        await env.summoner.summon(parent_id=None, task='second', agent_id=node.short_id)
        return node

    node = asyncio.run(inner())
    assert node.runs == 2


def test_a_short_id_reaches_a_live_sibling(tmp_path):
    siblings = 'channel:\n  mode: bidirectional\n  siblings: [worker]\n'
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'reader': {'extra': f'role: subagent\n{siblings}'}, 'worker': {}},
    )
    tree = env.tree()
    root = tree.ensure_root('wizard')
    reader = tree.add(parent_id=root.id, name='reader', role=node_role(), task='a')
    worker = tree.add(parent_id=root.id, name='worker', role=node_role(), task='b')
    reader.channel = env.registry.require('reader').channel
    worker.run = _LiveRun()

    message = env.summoner.send_message(sender_id=reader.id, target_id=f'#{worker.short_id}', text='look')

    assert 'delivered' in message
    assert worker.run.enqueued == []
    assert tree.notifications.for_receiver(worker.id)[0].text == 'look'


def test_an_ambiguous_prefix_is_refused_instead_of_guessing(tmp_path):
    """Two executions of the same subagent must not be confusable."""
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    first = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    second = tree.add(parent_id=root.id, name='worker', role=node_role(), task='b')
    # Force the collision a prefix would create.
    shared = first.id[:4]
    second.id = shared + second.id[4:]
    first.id = shared + first.id[4:]

    with pytest.raises(SummonError, match='more than one agent'):
        env.summoner.kill(None, shared)


def test_an_unknown_id_says_what_an_id_is(tmp_path):
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})

    with pytest.raises(SummonError, match='not the name of the profile'):
        env.summoner.kill(None, 'nope')
