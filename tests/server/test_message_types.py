"""Types survive real enqueue delivery, serialization, continuation and storage."""

import asyncio
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest, UserPromptPart
from pydantic_ai.models.test import TestModel

from src.core.agents.message_types import MessageTypes
from src.core.agents.origin import origin
from src.core.agents.request import request
from src.core.agents.type_of import type_of
from src.core.sessions.session_store import SessionStore
from src.core.storage.database import Database
from src.server.api.views import transcript, transcript_window


def _report() -> dict:
    return {
        'node_id': 'child',
        'label': 'Scout',
        'short_id': 'child',
        'name': 'scout',
        'status': 'failed',
        'text': 'failure details',
    }


def test_types_survive_active_delivery_reopen_and_next_run(tmp_path: Path) -> None:
    async def run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        agent = Agent(TestModel(call_tools=['wait'], custom_output_text='done'))

        @agent.tool_plain
        async def wait() -> str:
            started.set()
            await asyncio.wait_for(release.wait(), timeout=5)
            return 'done'

        async with agent.iter('initial', capabilities=[MessageTypes()]) as execution:

            async def iterate() -> None:
                async for _ in execution:
                    pass

            task = asyncio.create_task(iterate())
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                execution.enqueue(request('identical text', origin('subagent_report', reports=[_report()])))
                execution.enqueue('identical text')
                execution.enqueue(request('internal', origin('internal_notice')))
                release.set()
                await asyncio.wait_for(task, timeout=5)
            finally:
                release.set()
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        history = execution.all_messages()
        roundtrip = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json(history))
        projected = transcript(roundtrip)
        assert [item['kind'] for item in projected].count('subagent_report') == 1
        assert any(item['kind'] == 'user' and item['text'] == 'identical text' for item in projected)
        assert not any(item.get('text') == 'internal' for item in projected)
        assert next(item for item in projected if item['kind'] == 'subagent_report')['text'] == 'failure details'

        store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'home')
        database = store.database
        try:
            session = store.create_session()
            session.messages = roundtrip
            store.save(session)
            loaded = store.load(session.id)
            assert loaded is not None
            assert transcript(loaded.messages) == projected
            continued = await agent.run('continue', message_history=loaded.messages, capabilities=[MessageTypes()])
            continued_items = transcript(continued.all_messages())[: len(projected)]
            assert [{key: value for key, value in item.items() if key != 'index'} for item in continued_items] == [
                {key: value for key, value in item.items() if key != 'index'} for item in projected
            ]
        finally:
            Database.forget(database.path)

    asyncio.run(run())


def test_initial_types_and_hidden_notices_are_counted_by_pagination() -> None:
    async def run() -> None:
        agent = Agent(TestModel(custom_output_text='done'))
        result = await agent.run(
            'callback envelope',
            capabilities=[MessageTypes(initial=origin('subagent_report', reports=[_report(), _report()]))],
        )
        messages = result.all_messages()
        whole = transcript(messages)
        assert [item['kind'] for item in whole] == ['subagent_report', 'subagent_report', 'assistant']
        for before in range(5):
            page = transcript_window(messages, limit=1, before=before)
            assert len(page.items) <= 1
            assert page.items == whole[max(0, min(before, len(whole)) - 1) : min(before, len(whole))]

    asyncio.run(run())


def test_merged_history_types_are_restored_without_typing_old_user_parts() -> None:
    async def run() -> None:
        first = request('report', origin('subagent_report', reports=[_report()]))
        second = ModelRequest(parts=[UserPromptPart('human')])
        agent = Agent(TestModel())
        result = await agent.run('next', message_history=[first, second], capabilities=[MessageTypes()])
        kinds = [item['kind'] for item in transcript(result.all_messages())]
        assert kinds[:3] == ['subagent_report', 'user', 'user']
        assert type_of(second, second.parts[0]) == origin()

    asyncio.run(run())


def test_mixed_report_preserves_human_attachments() -> None:
    from pydantic_ai.messages import BinaryContent

    from src.core.agents.mark import mark

    part = UserPromptPart(['internal envelope and human text', BinaryContent(b'image', media_type='image/png')])
    message = ModelRequest(parts=[part])
    mark(message, part, {**origin('subagent_report', reports=[_report()]), 'user_text': 'human text'})
    items = transcript([message])
    assert [item['kind'] for item in items] == ['subagent_report', 'user']
    assert items[1]['text'] == 'human text'
    assert len(items[1]['attachments']) == 1


def test_concurrent_runs_keep_their_own_initial_types() -> None:
    async def run() -> None:
        agent = Agent(TestModel(custom_output_text='done'))
        report, human = await asyncio.gather(
            agent.run('same', capabilities=[MessageTypes(initial=origin('subagent_report', reports=[_report()]))]),
            agent.run('same', capabilities=[MessageTypes()]),
        )
        assert transcript(report.all_messages())[0]['kind'] == 'subagent_report'
        assert transcript(human.all_messages())[0]['kind'] == 'user'

    asyncio.run(run())


def test_failed_model_request_keeps_the_initial_type() -> None:
    from typing import Any

    import pytest
    from pydantic_ai.models.function import FunctionModel

    def fail(_messages: Any, _info: Any) -> Any:
        raise RuntimeError('model failed')

    async def run() -> None:
        agent = Agent(FunctionModel(fail))
        async with agent.iter(
            'internal callback', capabilities=[MessageTypes(initial=origin('subagent_report', reports=[_report()]))]
        ) as execution:
            with pytest.raises(RuntimeError, match='model failed'):
                async for _ in execution:
                    pass
        assert transcript(execution.all_messages())[0]['kind'] == 'subagent_report'

    asyncio.run(run())
