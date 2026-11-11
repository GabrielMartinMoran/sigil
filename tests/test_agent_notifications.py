"""Notifications are ordered context state; parent instructions remain steering."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from src.engine.agents.agent import create_agent
from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.tree.models.agent_node import AgentNode
from src.engine.agents.tree.models.summon_error import SummonError
from src.engine.agents.tree.rendering import render_notifications
from src.engine.agents.tree.summoner import SigilDeps
from src.engine.agents.tree.tools import register_tree_tools
from src.engine.agents.tree.tree import AgentTree
from src.engine.sessions.session_store import SessionStore
from tests.test_agents_tree_control import Env, _LiveRun, _root_calling, make_env


@pytest.fixture()
def env(tmp_path: Path) -> Env:
    return make_env(
        tmp_path,
        root=_root_calling('list_agents', {}),
        profiles={'worker': {'extra': 'role: subagent\nchannel: bidirectional\n'}},
        root_run=lambda: _LiveRun(),
    )


def add_worker(env: Env, parent_id: str | None = None) -> AgentNode:
    tree = env.tree()
    node = tree.add(parent_id=parent_id or tree.root_id, name='worker', role=AgentRole.SUBAGENT, task='work')
    node.channel = env.registry.require('worker').channel
    node.run = _LiveRun()
    return node


def test_should_store_notice_without_steering_when_child_messages_parent(env: Env) -> None:
    run = _LiveRun()
    env.summoner.listen(root_run=lambda: run)
    worker = add_worker(env)

    result = env.summoner.send_message(sender_id=worker.id, target_id=env.tree().root_id, text='found it')

    notices = env.tree().notifications.for_receiver(env.tree().root_id)
    assert len(notices) == 1
    assert notices[0].text == 'found it'
    assert notices[0].sender_id == worker.id
    assert not notices[0].acknowledged
    assert notices[0].id in result
    assert run.enqueued == []


def test_should_keep_instructions_out_of_table_when_parent_steers_child(env: Env) -> None:
    worker = add_worker(env)

    env.summoner.send_message(sender_id=None, target_id=worker.id, text='change direction')

    assert 'change direction' in worker.run.enqueued[0]
    assert env.tree().notifications.for_receiver(worker.id) == ()


def test_should_keep_content_and_order_when_acknowledged_out_of_order(env: Env) -> None:
    worker = add_worker(env)
    for text in ('first | detail\nnext line', 'second', 'third'):
        env.summoner.send_message(sender_id=worker.id, target_id=env.tree().root_id, text=text)
    notices = env.tree().notifications.for_receiver(env.tree().root_id)
    ids = [notice.id for notice in notices]

    result = env.summoner.ack_notifications(receiver_id=None, notification_ids=[ids[2], ids[0], ids[2]])
    env.summoner.ack_notifications(receiver_id=None, notification_ids=[ids[0]])
    block = render_notifications(env.tree(), None)

    assert 'ACK' in result
    assert 'first | detail' not in result
    assert [notice.acknowledged for notice in env.tree().notifications.for_receiver(env.tree().root_id)] == [
        True,
        False,
        True,
    ]
    assert block.index(ids[0]) < block.index(ids[1]) < block.index(ids[2])
    assert 'first &#124; detail<br>next line' in block
    assert 'second' in block and 'third' in block
    assert 'Pending' in block and 'ACK' in block
    assert env.tree().notifications.for_receiver(worker.id) == ()


def test_should_reject_ack_atomically_when_any_id_is_unknown_or_belongs_to_another_receiver(env: Env) -> None:
    worker = add_worker(env)
    env.summoner.send_message(sender_id=worker.id, target_id=env.tree().root_id, text='report')
    notice = env.tree().notifications.for_receiver(env.tree().root_id)[0]

    for receiver, ids in ((None, [notice.id, 'n-unknown']), (worker.id, [notice.id]), (None, [])):
        with pytest.raises(SummonError):
            env.summoner.ack_notifications(receiver_id=receiver, notification_ids=ids)
    assert not env.tree().notifications.for_receiver(env.tree().root_id)[0].acknowledged


def test_should_message_direct_parent_but_not_root_ancestor_when_nested(env: Env) -> None:
    parent = add_worker(env)
    child = add_worker(env, parent.id)

    env.summoner.send_message(sender_id=child.id, target_id=parent.id, text='nested report')

    assert env.tree().notifications.for_receiver(parent.id)[0].text == 'nested report'
    with pytest.raises(SummonError, match='none of those'):
        env.summoner.send_message(sender_id=child.id, target_id=env.tree().root_id, text='skip parent')


def test_should_restore_ack_content_and_sequence_when_session_reloads(env: Env, tmp_path: Path) -> None:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'saved')
    session = store.create_session()
    env.trees.bind(session, root_name='wizard')
    worker = add_worker(env)
    env.summoner.send_message(sender_id=worker.id, target_id=env.tree().root_id, text='kept')
    first = env.tree().notifications.for_receiver(env.tree().root_id)[0]
    store.save(session)
    env.summoner.ack_notifications(receiver_id=None, notification_ids=[first.id])
    store.save(session)
    store.save(session)

    loaded = store.load(session.id)
    assert loaded is not None
    env.trees.bind(loaded, root_name='wizard')
    env.summoner.send_message(sender_id=worker.id, target_id=env.tree().root_id, text='next')
    notices = env.tree().notifications.for_receiver(env.tree().root_id)

    assert [(notice.text, notice.acknowledged, notice.sequence) for notice in notices] == [
        ('kept', True, 1),
        ('next', False, 2),
    ]
    assert 'kept' in render_notifications(env.tree(), None)
    restored = AgentTree.from_dict(env.tree().to_dict())
    assert restored is not None
    assert env.tree().to_dict() == restored.to_dict()
    assert store.database.value('SELECT count(*) FROM agent_notifications') == 1
    store.delete(session.id)
    assert store.database.value('SELECT count(*) FROM agent_notifications') == 0


def test_should_refresh_table_without_user_message_when_model_acks(env: Env) -> None:
    worker = add_worker(env)
    env.summoner.send_message(sender_id=worker.id, target_id=env.tree().root_id, text='persistent context')
    notice = env.tree().notifications.for_receiver(env.tree().root_id)[0]
    snapshots: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        snapshots.append(str(messages[-1].parts[-1].content))
        if len(snapshots) == 1:
            return ModelResponse(parts=[ToolCallPart('ack_notifications', {'notification_ids': [notice.id]})])
        return ModelResponse(parts=[TextPart('done')])

    agent = create_agent(
        None, model=FunctionModel(model), node_blocks=lambda node_id: render_notifications(env.tree(), node_id)
    )
    register_tree_tools(agent, env.summoner)
    result = asyncio.run(agent.run('go', deps=SigilDeps()))

    assert 'Pending' in snapshots[0]
    assert f'| {notice.id} |' in snapshots[1]
    assert '| ACK |' in snapshots[1]
    assert 'persistent context' in snapshots[1]
    user_parts = [
        part for message in result.all_messages() for part in message.parts if part.part_kind == 'user-prompt'
    ]
    assert [part.content for part in user_parts] == ['go']
    assert env.tree().notifications.for_receiver(env.tree().root_id)[0].acknowledged


def test_should_use_session_sequence_across_receivers_and_equal_timestamps(env: Env) -> None:
    first = add_worker(env)
    second = add_worker(env, first.id)
    log = env.tree().notifications

    log.publish(second, first.id, 'earlier')
    root = env.tree().root
    assert root is not None
    log.publish(first, root.id, 'middle')
    log.publish(second, first.id, 'later')

    from dataclasses import replace

    from src.engine.agents.tree.notifications import Notifications

    log = Notifications([replace(entry, created_at=1.0) for entry in reversed(log.all())])
    assert [entry.sequence for entry in log.for_receiver(first.id)] == [1, 3]
    assert [entry.sequence for entry in log.for_receiver(env.tree().root_id)] == [2]
    assert [entry.sequence for entry in log.all()] == [1, 2, 3]


def test_should_render_for_root_and_isolate_sessions_in_composition(env: Env) -> None:
    from src.engine.bootstrap import Composition

    worker = add_worker(env)
    env.summoner.send_message(sender_id=worker.id, target_id=env.tree().root_id, text='root-only notice')
    composition = object.__new__(Composition)
    composition.agent_trees = env.trees
    composition.agent_registry = env.registry
    composition.summoner = env.summoner

    assert 'root-only notice' in Composition.node_blocks(composition, None)
    assert 'root-only notice' not in Composition.node_blocks(composition, worker.id)
    env.trees.bind(SessionStore(cwd=env.tmp, base_dir=env.tmp / 'another').create_session(), root_name='wizard')
    assert 'root-only notice' not in Composition.node_blocks(composition, None)


def test_should_upgrade_version_two_without_losing_history(tmp_path: Path) -> None:
    import sqlite3

    from src.engine.storage.database import Database
    from src.engine.storage.schema import SCHEMA, USER_VERSION

    path = tmp_path / 'older.db'
    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.execute('DROP TABLE agent_notifications')
        connection.execute('ALTER TABLE sessions DROP COLUMN allow_external_files')
        connection.execute('ALTER TABLE sessions DROP COLUMN mcp_overrides')
        connection.execute("INSERT INTO app_state VALUES ('kept', 'old value')")
        connection.execute('PRAGMA user_version = 2')

    connection.close()
    database = Database(path)
    try:
        assert database.value('PRAGMA user_version') == USER_VERSION
        assert database.value("SELECT value FROM app_state WHERE key = 'kept'") == 'old value'
        assert database.value('SELECT count(*) FROM agent_notifications') == 0
    finally:
        database.connection.close()


def test_should_include_notices_in_session_export(env: Env, tmp_path: Path) -> None:
    import json

    from src.engine.storage.maintenance import export_session

    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'export')
    session = store.create_session()
    env.trees.bind(session, root_name='wizard')
    worker = add_worker(env)
    env.summoner.send_message(sender_id=worker.id, target_id=env.tree().root_id, text='exported')
    store.save(session)

    exported = json.loads(export_session(store.database, store.blobs, session.id))

    assert exported['notifications'][0]['content'] == 'exported'
    assert exported['notifications'][0]['acknowledged'] == 0


def test_should_preserve_notification_order_when_tools_are_called_together(env: Env) -> None:
    worker = add_worker(env)
    calls = 0

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart('send_message', {'agent_id': env.tree().root_id, 'message': text})
                    for text in ('first', 'second', 'third')
                ]
            )
        return ModelResponse(parts=[TextPart('done')])

    agent = create_agent(None, model=FunctionModel(model))
    register_tree_tools(agent, env.summoner)
    asyncio.run(agent.run('notify', deps=SigilDeps(node_id=worker.id)))

    assert [(entry.sequence, entry.text) for entry in env.tree().notifications.all()] == [
        (1, 'first'),
        (2, 'second'),
        (3, 'third'),
    ]
