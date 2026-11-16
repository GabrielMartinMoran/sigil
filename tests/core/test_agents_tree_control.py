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
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo

from src.core.agents.agent_factory import AgentFactory
from src.core.agents.agent_spec import AgentSpec
from src.core.agents.notice import PREFIX
from src.core.agents.tree.agent_tree import AgentTree
from src.core.agents.tree.models.node_status import NodeStatus
from src.core.agents.tree.models.summon_error import SummonError
from src.core.agents.tree.rendering import render_peers
from src.core.agents.tree.tools import register_tree_tools
from src.core.context.context_view_capability import ContextViewCapability
from src.core.context.meter.context_meter import ContextMeter
from src.core.context.strategy.append_only_strategy import AppendOnlyStrategy
from tests.support.agents_tree_control import (
    _LiveRun,
    _model,
    _root_calling,
    _root_with_script,
    _texts,
    _tool_returns,
    make_env,
    node_role,
    run_root,
)

# --- max_steps ---------------------------------------------------------------


def test_a_subagent_that_runs_out_of_steps_still_reports(tmp_path: Path) -> None:
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

    def install(target) -> None:
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


def test_an_exhausted_subagent_can_be_resumed_with_its_id(tmp_path: Path) -> None:
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


def test_a_resume_of_an_unknown_id_explains_what_the_id_is(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}))

    with pytest.raises(SummonError, match='not the name of the profile'):
        asyncio.run(env.summoner.summon(parent_id=None, task='go', agent_id='nope'))


def test_a_running_node_cannot_be_resumed(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='go')

    with pytest.raises(SummonError, match='still working'):
        asyncio.run(env.summoner.summon(parent_id=None, task='more', agent_id=node.id))


def test_two_summons_without_an_id_are_two_nodes(tmp_path: Path) -> None:
    env = make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'worker': {}},
        scripts={'worker': lambda m, i: ModelResponse(parts=[TextPart('done')])},
    )

    async def inner() -> None:
        await env.summoner.summon(parent_id=None, name='worker', task='a')
        await env.summoner.summon(parent_id=None, name='worker', task='b')

    asyncio.run(inner())
    nodes = env.tree().nodes()
    assert [node.name for node in nodes] == ['wizard', 'worker', 'worker']
    assert nodes[1].id != nodes[2].id


# --- killing -----------------------------------------------------------------


def test_killing_a_node_stops_it_and_everything_under_it(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}, 'deeper': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    parent = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    child = tree.add(parent_id=parent.id, name='deeper', role=node_role(), task='b')

    message = env.summoner.kill(None, parent.id)

    assert 'stopped' in message
    assert parent.status is NodeStatus.CANCELLED
    assert child.status is NodeStatus.CANCELLED


def test_killing_a_finished_node_is_a_no_op(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    node.finish('done')

    assert 'had already finished' in env.summoner.kill(None, node.id)


def test_a_node_cannot_kill_its_own_parent(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}, 'deeper': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    parent = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    child = tree.add(parent_id=parent.id, name='deeper', role=node_role(), task='b')

    with pytest.raises(SummonError, match='cannot stop it'):
        env.summoner.kill(child.id, parent.id)


def test_the_root_cannot_be_killed(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}))
    root = env.tree().ensure_root('wizard')

    with pytest.raises(SummonError, match='stop the turn instead'):
        env.summoner.kill(None, root.id)


def test_a_killed_sync_summon_does_not_cancel_the_callers_turn(tmp_path: Path) -> None:
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


def test_a_blocking_call_can_be_moved_to_the_background(tmp_path: Path) -> None:
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


def test_a_finished_node_cannot_be_backgrounded(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    node.finish('done')

    with pytest.raises(SummonError, match='already finished'):
        env.summoner.detach(node.id)


# --- the channel -------------------------------------------------------------


def test_a_unidirectional_child_cannot_talk_back(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')

    with pytest.raises(SummonError, match='does not allow talking to your parent'):
        env.summoner.send_message(sender_id=node.id, target_id=root.id, text='hi')


def test_a_bidirectional_child_reaches_its_parent_when_it_is_working(tmp_path: Path) -> None:
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


def test_a_parent_steers_a_running_child_with_an_instruction(tmp_path: Path) -> None:
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
    from src.core.agents.type_of import type_of

    delivered = run.enqueued[0]
    assert 'go left instead' in delivered.parts[0].content
    assert 'instruction from your parent' in delivered.parts[0].content
    assert type_of(delivered, delivered.parts[0])['type'] == 'agent_message'


def test_a_parent_cannot_message_a_child_that_is_not_running(tmp_path: Path) -> None:
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


def test_a_finished_node_keeps_nothing_waiting(tmp_path: Path) -> None:
    """A queue nobody promised to drain is a queue that grows in the dark."""
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='a')
    node.finish('done')

    with pytest.raises(SummonError):
        env.summoner.send_message(sender_id=root.id, target_id=node.id, text='one more thing')

    assert not hasattr(node, 'inbox')


def test_a_sibling_needs_its_name_in_the_channel(tmp_path: Path) -> None:
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


def test_a_sibling_that_is_not_running_cannot_be_messaged(tmp_path: Path) -> None:
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


def test_a_stranger_is_not_reachable(tmp_path: Path) -> None:
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


def test_the_peers_block_lists_the_parent_and_the_ids(tmp_path: Path) -> None:
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


def test_the_peers_block_shows_only_the_siblings_the_channel_names(tmp_path: Path) -> None:
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


def test_the_root_gets_no_peers_block(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}))
    tree = env.tree()
    root = tree.ensure_root('wizard')

    assert render_peers(tree, None) == ''
    assert render_peers(tree, root.id) == ''


def test_a_child_cannot_message_a_parent_that_is_not_in_a_turn(tmp_path: Path) -> None:
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


def test_answering_a_finished_child_resumes_it_with_the_message(tmp_path: Path) -> None:
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


def test_list_agents_shows_the_ids_and_who_summoned_who(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})
    tree = env.tree()
    root = tree.ensure_root('wizard')
    node = tree.add(parent_id=root.id, name='worker', role=node_role(), task='read it')
    node.finish('done')

    from src.core.agents.tree.tools import render_tree_table

    table = render_tree_table(tree, root.id)
    assert 'id | agent | state' in table
    assert node.short_id in table
    assert '(you)' in table
    assert 'read it' in table


def test_list_agents_on_a_session_with_no_subagents_says_so(tmp_path: Path) -> None:
    from src.core.agents.tree.tools import render_tree_table

    tree = AgentTree()
    tree.ensure_root('wizard')
    assert 'no subagents' in render_tree_table(tree, None)


def test_a_node_that_ends_stops_what_it_had_running_under_it(tmp_path: Path) -> None:
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


def test_a_short_id_is_enough_to_message_or_resume(tmp_path: Path) -> None:
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


def test_a_short_id_reaches_a_live_sibling(tmp_path: Path) -> None:
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


def test_an_ambiguous_prefix_is_refused_instead_of_guessing(tmp_path: Path) -> None:
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


def test_an_unknown_id_says_what_an_id_is(tmp_path: Path) -> None:
    env = make_env(tmp_path, root=_root_calling('list_agents', {}), profiles={'worker': {}})

    with pytest.raises(SummonError, match='not the name of the profile'):
        env.summoner.kill(None, 'nope')


# --- context -----------------------------------------------------------------


def test_a_subagent_on_the_session_model_keeps_the_known_window(tmp_path: Path) -> None:
    """A node that inherits the model must not lose the window the meter holds.

    A subagent whose profile configures no model sends to the model the session is on,
    so the window the meter already has *is* its window. Replacing it with an unknown
    value refused every one of those requests with "Context window is unknown", which
    is the error a user gets the moment a subagent is summoned on a model the
    catalogue did not describe.
    """
    capability = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter(window=100_000))
    env = make_env(
        tmp_path,
        root=_root_calling('summon', {'agent': 'worker', 'task': 'read it'}),
        profiles={'worker': {'extra': 'role: subagent\n'}},
        scripts={'worker': lambda messages, info: ModelResponse(parts=[TextPart('read half the tree.')])},
        capabilities=[capability],
    )

    messages = run_root(env)

    node = env.tree().nodes()[1]
    assert node.error is None, node.error
    assert node.status is NodeStatus.DONE
    assert 'read half the tree.' in str(_tool_returns(messages)[0].content)


def test_a_subagent_with_an_unknown_window_does_not_inherit_the_root_window(tmp_path: Path) -> None:
    """A different model may run, but its unknown window remains unknown."""
    from src.core.agents.tree.agent_run_model import AgentRunModel

    notices: list[tuple[str, str]] = []
    capability = ContextViewCapability(
        strategy=AppendOnlyStrategy(),
        meter=ContextMeter(window=100_000),
        on_unknown_window=lambda node, model: notices.append((node, model)),
    )
    other = _model(lambda messages, info: ModelResponse(parts=[TextPart('completed')]))
    env = make_env(
        tmp_path,
        root=_root_calling('summon', {'agent': 'worker', 'task': 'read it'}),
        profiles={'worker': {'extra': 'role: subagent\n'}},
        scripts={'worker': lambda messages, info: ModelResponse(parts=[TextPart('never used')])},
        capabilities=[capability],
        agent_model=lambda name: AgentRunModel(other, None, 'other:model', window=None),
    )

    run_root(env)

    node = env.tree().nodes()[1]
    assert node.status is NodeStatus.DONE
    assert capability.budget_for_node(node.id).window is None
    assert notices == [(node.id, other.model_name)]


@pytest.mark.parametrize('mode', ['sync', 'async'])
def test_should_await_model_resolution_on_spawn_and_resume(tmp_path: Path, mode: str) -> None:
    from src.core.agents.tree.agent_run_model import AgentRunModel

    resolutions: list[str] = []
    worker = _model(lambda messages, info: ModelResponse(parts=[TextPart('finished')]))

    async def resolve(name: str) -> AgentRunModel:
        resolutions.append(name)
        return AgentRunModel(worker, None, 'Personal:deepseek-v4.1-flash', window=1_000_000)

    capability = ContextViewCapability(strategy=AppendOnlyStrategy(), meter=ContextMeter())
    env = make_env(
        tmp_path,
        root=lambda messages, info: ModelResponse(parts=[TextPart('root')]),
        profiles={'worker': {'extra': 'role: subagent\n'}},
        scripts={'worker': lambda messages, info: ModelResponse(parts=[TextPart('unused')])},
        capabilities=[capability],
        agent_model=resolve,
    )
    env.tree().ensure_root('wizard')

    async def exercise() -> None:
        await env.summoner.summon(parent_id=None, name='worker', task='read it', mode=mode)
        await asyncio.wait_for(asyncio.gather(*env.summoner._tasks.values()), timeout=5)
        node = env.tree().nodes()[1]
        assert node.status is NodeStatus.DONE, node.error
        assert node.model == 'Personal:deepseek-v4.1-flash'
        await env.summoner.summon(parent_id=None, agent_id=node.id, task='continue', mode=mode)
        await asyncio.wait_for(asyncio.gather(*env.summoner._tasks.values()), timeout=5)
        assert node.status is NodeStatus.DONE, node.error

    asyncio.run(exercise())
    assert resolutions == ['worker', 'worker']


def test_should_preserve_cancellation_during_model_resolution(tmp_path: Path) -> None:
    from src.core.agents.tree.agent_run_model import AgentRunModel

    async def resolve(name: str) -> AgentRunModel:
        raise asyncio.CancelledError()

    env = make_env(
        tmp_path,
        root=lambda messages, info: ModelResponse(parts=[TextPart('root')]),
        profiles={'worker': {'extra': 'role: subagent\n'}},
        agent_model=resolve,
    )
    env.tree().ensure_root('wizard')
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(env.summoner.summon(parent_id=None, name='worker', task='read it'))
    assert len(env.tree().nodes()) == 1


def test_external_task_cancellation_marks_the_node_cancelled_without_a_report(tmp_path: Path) -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def blocked(_messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError('The task must be cancelled before producing a response')

        env = make_env(
            tmp_path,
            root=_root_calling('list_agents', {}),
            profiles={'worker': {}},
            scripts={'worker': blocked},
        )
        await env.summoner.summon(parent_id=None, name='worker', task='wait', mode='async')
        node = env.tree().nodes()[1]
        handle = env.summoner._tasks[node.id]
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            handle.cancel()
            with pytest.raises(asyncio.CancelledError):
                await handle
            assert node.status is NodeStatus.CANCELLED
            assert env.summoner.take_reports() == []
        finally:
            handle.cancel()
            await asyncio.gather(handle, return_exceptions=True)

    asyncio.run(scenario())
