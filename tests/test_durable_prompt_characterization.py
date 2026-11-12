"""Characterize installed agent and SQLite prompt identity, not durable acceptance."""

import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent, ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.models.test import TestModel

from src.engine.agents.rollback import Checkpoints, Rollback, prompt_of_turn
from src.engine.sessions.session_store import SessionStore
from src.engine.storage.database import Database, database_path

TEXT = 'describe this image'
IMAGE = BinaryContent(data=b'characterization-image', media_type='image/png')


def test_normal_prompt_reopens_with_checkpoint(tmp_path: Path) -> None:
    async def run() -> None:
        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'database')
        session = store.create_session()
        result = await Agent(TestModel(custom_output_text='ok')).run([TEXT, IMAGE])
        session.messages = result.all_messages()
        assert isinstance(session.messages[0], ModelRequest)
        assert isinstance(session.messages[1], ModelResponse)
        assert session.messages[0].run_id
        assert session.messages[0].run_id == session.messages[1].run_id
        store.save(session)
        Database.forget(database_path(tmp_path / 'database'))
        reopened = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'database')
        loaded = reopened.load(session.id)
        assert loaded is not None
        rows = reopened.database.query('SELECT seq, turn_id FROM messages ORDER BY seq')
        assert len(rows) == 2
        assert rows[0]['turn_id'] == rows[1]['turn_id']
        checkpoint = Checkpoints(reopened.database, reopened.blobs).of_turn(rows[0]['turn_id'])
        assert checkpoint is not None
        assert [point.turn_id for point in Rollback(reopened, workspace=tmp_path).points(session.id)] == [
            rows[0]['turn_id']
        ]
        assert sum(isinstance(part, UserPromptPart) for message in loaded.messages for part in message.parts) == 1
        assert loaded.messages[0].run_id == loaded.messages[1].run_id == session.messages[0].run_id
        prompt = prompt_of_turn(reopened.database, rows[0]['turn_id'])
        assert prompt is not None
        assert prompt[0] == TEXT
        assert prompt[1][0].data == IMAGE.data
        assert prompt[1][0].media_type == IMAGE.media_type
        Database.forget(database_path(tmp_path / 'database'))

    asyncio.run(run())


def test_stored_trailing_prompt_handoff_and_rollback(tmp_path: Path) -> None:
    async def run() -> None:
        base = tmp_path / 'database'
        store = SessionStore(cwd=tmp_path, base_dir=base)
        session = store.create_session()
        session.messages = [ModelRequest(parts=[UserPromptPart([TEXT, IMAGE])], run_id='stored-run')]
        store.save(session)
        before = store.database.query('SELECT seq, turn_id FROM messages ORDER BY seq')
        request_turn = str(before[0]['turn_id'])
        checkpoint = Checkpoints(store.database, store.blobs).of_turn(request_turn)
        assert checkpoint is not None
        assert prompt_of_turn(store.database, request_turn) == (TEXT, [IMAGE])
        assert [point.turn_id for point in Rollback(store, workspace=tmp_path).points(session.id)] == [request_turn]
        Database.forget(database_path(base))

        store = SessionStore(cwd=tmp_path, base_dir=base)
        session = store.load(session.id)
        assert session is not None
        result = await Agent(TestModel(custom_output_text='ok')).run(None, message_history=session.messages)
        session.messages = result.all_messages()
        assert len(session.messages) == 2
        assert session.messages[0].run_id == 'stored-run'
        response_run = session.messages[1].run_id
        assert response_run and response_run != 'stored-run'
        store.save(session)
        Database.forget(database_path(base))

        store = SessionStore(cwd=tmp_path, base_dir=base)
        session = store.load(session.id)
        assert session is not None
        rows = store.database.query('SELECT seq, turn_id FROM messages ORDER BY seq')
        assert len(rows) == 2
        assert [message.run_id for message in session.messages] == ['stored-run', response_run]
        assert sum(isinstance(part, UserPromptPart) for message in session.messages for part in message.parts) == 1
        assert rows[0]['turn_id'] == request_turn
        assert rows[1]['turn_id'] != request_turn
        checkpoints = Checkpoints(store.database, store.blobs)
        assert checkpoints.of_turn(request_turn) == checkpoint
        assert checkpoints.of_turn(rows[1]['turn_id']) is not None
        prompt = prompt_of_turn(store.database, request_turn)
        assert prompt is not None
        assert prompt[0] == TEXT
        assert len(prompt[1]) == 1
        assert prompt[1][0].data == IMAGE.data
        assert prompt[1][0].media_type == IMAGE.media_type
        rollback = Rollback(store, workspace=tmp_path)
        points = rollback.points(session.id)
        assert [point.turn_id for point in points] == [request_turn]
        outcome = rollback.to_turn(session.id, request_turn, files=False)
        assert outcome.messages_dropped == 2
        assert outcome.turns_dropped == 2
        restored = store.load(session.id)
        assert restored is not None
        assert restored.messages == []
        Database.forget(database_path(base))

    asyncio.run(run())
