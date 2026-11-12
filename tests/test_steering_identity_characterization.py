"""Characterize the installed steering event and persisted history boundary."""

import asyncio
from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel

from src.engine.attachments.image_attachment import ImageAttachment
from src.engine.sessions.session_store import SessionStore
from src.engine.storage.database import Database, database_path
from src.ui.app import SigilApp
from tests.ui_helpers import wait_for_turn


async def _characterize(tmp_path: Path) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    delivered: list[str] = []
    agent = Agent(TestModel(call_tools=['wait_for_it'], custom_output_text='done'))

    @agent.tool_plain
    async def wait_for_it() -> str:
        started.set()
        await release.wait()
        return 'tool result'

    base = tmp_path / 'database'
    store = SessionStore(cwd=tmp_path, base_dir=base)
    app = SigilApp(agent=agent, store=store, session=store.create_session())
    mark_delivered = app._mark_delivered

    def record_delivery(enqueue_id: str, chat: Any) -> None:
        delivered.append(enqueue_id)
        mark_delivered(enqueue_id, chat)

    app._mark_delivered = record_delivery
    image = ImageAttachment(number=1, data=b'identical-image', media_type='image/png', width=1, height=1)
    async with app.run_test():
        app.start_turn('initial')
        await asyncio.wait_for(started.wait(), timeout=5)
        app.steer('same [Image #1]', attachments=(image,))
        app.steer('same [Image #1]', attachments=(image,))
        ids = [entry.enqueue_id for entry in app._steering]
        assert len(ids) == 2 and all(ids) and len(set(ids)) == 2
        release.set()
        await wait_for_turn(app)
        assert delivered == ids

    messages = app._session.messages
    assert [type(message) for message in messages] == [
        ModelRequest,
        ModelResponse,
        ModelRequest,
        ModelRequest,
        ModelRequest,
        ModelResponse,
    ]
    assert [[type(part) for part in message.parts] for message in messages] == [
        [UserPromptPart],
        [ToolCallPart],
        [ToolReturnPart],
        [UserPromptPart],
        [UserPromptPart],
        [TextPart],
    ]
    parts = [part for message in messages for part in message.parts]
    prompts = [part for part in parts if isinstance(part, UserPromptPart)]
    assert len(prompts) == 3
    assert prompts[0].content == 'initial'
    assert prompts[1].content == prompts[2].content
    assert isinstance(prompts[1].content, list)
    assert prompts[1].content[0] == 'same [Image #1]'
    assert prompts[1].content[1].data == b'identical-image'
    result_index = next(i for i, part in enumerate(parts) if isinstance(part, ToolReturnPart))
    assert parts[result_index].content == 'tool result'
    assert parts[result_index + 1 : result_index + 3] == prompts[1:]
    session_id = app._session.id
    Database.forget(database_path(base))
    reopened = SessionStore(cwd=tmp_path, base_dir=base)
    loaded = reopened.load(session_id)
    assert loaded is not None
    assert loaded.messages == messages
    serialized = ModelMessagesTypeAdapter.dump_json(loaded.messages)
    assert all(str(enqueue_id).encode() not in serialized for enqueue_id in ids)
    Database.forget(database_path(base))


def test_identical_steers_events_and_reopened_history(tmp_path: Path) -> None:
    asyncio.run(_characterize(tmp_path))
