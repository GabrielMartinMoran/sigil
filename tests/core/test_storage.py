"""Storage: the schema, the delta write and the round trip.

The test that matters most is the round trip: a `list[ModelMessage]` written and read
back must be **equal**, including reasoning, tool calls, provider details and binary
content. Everything else is an optimization of that invariant.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import (
    BinaryImage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from src.engine.agents.profile.models.agent_role import AgentRole
from src.engine.agents.tree.tree import AgentTree
from src.engine.providers.domain.selection import Selection
from src.engine.sessions.session_store import SessionStore
from src.engine.storage.database import Database, database_path
from src.engine.storage.history import history_size


@pytest.fixture()
def store(tmp_path: Path) -> SessionStore:
    base = tmp_path / 'home'
    Database.forget(database_path(base))
    return SessionStore(cwd=tmp_path / 'workspace', base_dir=base)


def _rich_history() -> list:
    """Every shape that has to survive: reasoning, tools, images, provider details."""
    return [
        ModelRequest(parts=[UserPromptPart(content='look at this')], instructions='BASE', run_id='run-1'),
        ModelResponse(
            parts=[
                ThinkingPart(content='let me think', signature='sig-1', provider_name='anthropic'),
                TextPart(content='sure', provider_name='anthropic', provider_details={'x': 1}),
                ToolCallPart(tool_name='read_file', args={'path': 'a.py'}, tool_call_id='call-1'),
            ],
            run_id='run-1',
        ),
        ModelRequest(
            parts=[ToolReturnPart(tool_name='read_file', content='contents', tool_call_id='call-1')],
            run_id='run-1',
        ),
        ModelResponse(parts=[TextPart(content='done')], run_id='run-1'),
        ModelRequest(
            parts=[UserPromptPart(content=[BinaryImage(data=b'\x89PNG\r\n', media_type='image/png')])],
            instructions='BASE\nSKILL',
            run_id='run-2',
        ),
        ModelResponse(parts=[TextPart(content='and this')], run_id='run-2'),
    ]


# --- the round trip -----------------------------------------------------------


def test_history_round_trips_exactly(store: SessionStore):
    session = store.create_session()
    session.messages = _rich_history()
    store.save(session)

    loaded = store.load(session.id)
    assert loaded is not None
    assert loaded.messages == session.messages


def test_the_root_is_an_execution_like_any_other(store: SessionStore):
    session = store.create_session()
    session.messages = _rich_history()
    store.save(session)

    assert store.database.value('SELECT count(*) FROM messages WHERE execution_id = ?', (session.root_id,)) == len(
        session.messages
    )
    # No second copy on the session row: the transcript lives in the execution.
    assert store.database.value('SELECT count(*) FROM messages WHERE execution_id != ?', (session.root_id,)) == 0


def test_a_session_always_has_exactly_one_root(store: SessionStore):
    session = store.create_session()
    store.save(session)
    store.save(session)

    assert (
        store.database.value(
            'SELECT count(*) FROM agent_executions WHERE session_id = ? AND parent_id IS NULL', (session.id,)
        )
        == 1
    )


def test_a_root_id_is_derived_from_the_session(store: SessionStore):
    session = store.create_session()
    store.save(session)
    assert (
        store.database.value('SELECT id FROM agent_executions WHERE session_id = ?', (session.id,)) == session.root_id
    )


# --- the delta write ----------------------------------------------------------


def test_appending_does_not_rewrite_the_history(store: SessionStore):
    session = store.create_session()
    session.messages = _rich_history()
    store.save(session)
    before = history_size(store.database, session.root_id)

    session.messages.append(ModelRequest(parts=[UserPromptPart(content='more')], run_id='run-3'))
    session.messages.append(ModelResponse(parts=[TextPart(content='ok')], run_id='run-3'))
    store.save(session)

    assert history_size(store.database, session.root_id) == before + 2
    assert store.load(session.id).messages == session.messages


def test_saving_twice_is_idempotent(store: SessionStore):
    """Writing a history twice must produce the same rows as writing it once."""
    session = store.create_session()
    session.messages = _rich_history()
    store.save(session)
    first = history_size(store.database, session.root_id)

    store.save(session)
    store.save(session)

    assert history_size(store.database, session.root_id) == first


def test_a_shorter_history_truncates_the_rows(store: SessionStore):
    session = store.create_session()
    session.messages = _rich_history()
    store.save(session)

    session.messages = session.messages[:2]
    store.save(session)

    assert history_size(store.database, session.root_id) == 2
    assert store.load(session.id).messages == session.messages


def test_a_repaired_tail_is_rewritten(store: SessionStore):
    """`recover_history` rewrites the last rows: a blind append would keep both."""
    session = store.create_session()
    session.messages = _rich_history()
    store.save(session)

    last = session.messages[-1]
    last.parts = [TextPart(content='corrected')]
    store.save(session)

    loaded = store.load(session.id)
    assert loaded.messages[-1].parts[0].content == 'corrected'
    assert history_size(store.database, session.root_id) == len(session.messages)


# --- the turns ----------------------------------------------------------------


def test_a_turn_is_a_row(store: SessionStore):
    session = store.create_session()
    session.messages = _rich_history()
    store.save(session)

    turns = store.database.query('SELECT id FROM turns WHERE execution_id = ? ORDER BY started_at', (session.root_id,))
    assert len(turns) == 2  # run-1 and run-2
    # Every message belongs to one.
    assert store.database.value('SELECT count(*) FROM messages WHERE turn_id IS NULL') == 0


def test_at_most_one_turn_is_open_per_execution(store: SessionStore):
    import sqlite3

    session = store.create_session()
    store.save(session)
    store.database.execute(
        "INSERT INTO turns (id, execution_id, origin, started_at) VALUES ('open-1', ?, 'user', 't')",
        (session.root_id,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.database.execute(
            "INSERT INTO turns (id, execution_id, origin, started_at) VALUES ('open-2', ?, 'user', 't')",
            (session.root_id,),
        )


def test_a_turn_cannot_half_close(store: SessionStore):
    """`finished_at` and `outcome` are one fact, and the CHECK keeps them together."""
    import sqlite3

    session = store.create_session()
    store.save(session)
    with pytest.raises(sqlite3.IntegrityError):
        store.database.execute(
            "INSERT INTO turns (id, execution_id, origin, started_at, outcome) VALUES ('t', ?, 'user', 't', 'done')",
            (session.root_id,),
        )


# --- the blobs ----------------------------------------------------------------


def test_repeated_instructions_are_one_blob(store: SessionStore):
    session = store.create_session()
    session.messages = [
        ModelRequest(parts=[UserPromptPart(content=f'q{index}')], instructions='SAME', run_id=f'r{index}')
        for index in range(5)
    ]
    store.save(session)

    assert store.database.value("SELECT count(*) FROM blobs WHERE kind = 'instructions'") == 1
    assert store.load(session.id).messages[0].instructions == 'SAME'


def test_the_instructions_come_back_without_being_re_encoded(store: SessionStore):
    """Assigning the shared string is what makes a load 36 ms and not 233 ms."""
    session = store.create_session()
    session.messages = _rich_history()
    store.save(session)

    loaded = store.load(session.id)
    assert loaded.messages[0].instructions == 'BASE'
    assert loaded.messages[4].instructions == 'BASE\nSKILL'
    assert loaded.messages[4].parts[0].content[0].data == b'\x89PNG\r\n'


# --- the listing --------------------------------------------------------------


def test_the_listing_does_not_read_the_transcripts(store: SessionStore):
    session = store.create_session()
    session.messages = _rich_history()
    session.title = 'A name'
    store.save(session)

    summaries = store.list_sessions()
    assert [summary.id for summary in summaries] == [session.id]
    assert summaries[0].display_title == 'A name'
    assert summaries[0].preview == 'look at this'
    assert summaries[0].profile == ''


def test_the_listing_carries_the_profile_of_the_root(store: SessionStore):
    session = store.create_session()
    session.agent = 'wizard'
    session.messages = _rich_history()
    store.save(session)

    assert store.list_sessions()[0].profile == 'wizard'


# --- the model choice ---------------------------------------------------------


def test_the_model_choice_travels_with_the_session(store: SessionStore):
    session = store.create_session()
    session.selection = Selection(account_id='acc-1', model='m-1', thinking='high', window=200_000, images=False)
    store.save(session)

    loaded = store.load(session.id)
    assert loaded.selection is not None
    assert loaded.selection.model == 'm-1'
    assert loaded.selection.window == 200_000
    assert loaded.selection.images is False


# --- the tree -----------------------------------------------------------------


def test_the_tree_and_its_transcripts_round_trip(store: SessionStore):
    session = store.create_session()
    session.messages = _rich_history()
    session.tree = AgentTree()
    session.tree.ensure_root('wizard', node_id=session.root_id)
    child = session.tree.add(parent_id=session.root_id, name='scout', role=AgentRole.SUBAGENT, task='look')
    child.finish('found it')
    child.messages = _rich_history()
    store.save(session)

    loaded = store.load(session.id)
    assert loaded.tree is not None
    assert [node.name for node in loaded.tree.nodes()] == ['wizard', 'scout']
    assert loaded.tree.node(child.id).messages == child.messages
    assert loaded.tree.node(child.id).result == 'found it'


# --- deletion -----------------------------------------------------------------


def test_deleting_a_session_takes_everything_that_hangs_from_it(store: SessionStore):
    session = store.create_session()
    session.messages = _rich_history()
    session.tree = AgentTree()
    session.tree.ensure_root('wizard', node_id=session.root_id)
    child = session.tree.add(parent_id=session.root_id, name='scout', role=AgentRole.SUBAGENT)
    child.messages = _rich_history()
    store.save(session)

    assert store.delete(session.id) is True

    for table in ('sessions', 'agent_executions', 'turns', 'messages'):
        assert store.database.value(f'SELECT count(*) FROM {table}') == 0
    assert store.database.query('PRAGMA foreign_key_check') == []


# --- one database, every workspace --------------------------------------------


def test_two_workspaces_share_the_database_and_not_the_listing(store: SessionStore, tmp_path: Path):
    mine = store.create_session()
    other = SessionStore(cwd=tmp_path / 'elsewhere', base_dir=tmp_path / 'home')
    theirs = other.create_session()

    assert other.database.path == store.database.path
    assert [summary.id for summary in store.list_sessions()] == [mine.id]
    assert [summary.id for summary in other.list_sessions()] == [theirs.id]


def test_the_last_session_is_remembered_per_workspace(store: SessionStore):
    first = store.create_session()
    second = store.create_session()

    assert store.load_last().id == second.id
    store.load(first.id)
    assert store.load_last().id == first.id
