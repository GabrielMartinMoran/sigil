"""Regression coverage for transcript spacing and last-submission recall."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.test import TestModel
from rich.console import Console

from src.engine.attachments.image_attachment import ImageAttachment
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import AssistantBlock, ChatLog, ToolCallBlock, ToolGroupHeader, UserMessage
from src.ui.widgets.prompt_input import PromptInput


@pytest.fixture
def app(tmp_path: Path) -> Iterator[SigilApp]:
    store = SessionStore(cwd=tmp_path, base_dir=tmp_path / 'state')
    yield SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
    )


def test_should_omit_command_hint_from_collapsed_reasoning() -> None:
    block = AssistantBlock()
    block.add_thinking('one\ntwo')
    assert '2 lines' in block._collapsed_marker().plain
    assert '/reasoning-visibility' not in block._collapsed_marker().plain


def test_should_render_done_without_reverse_video() -> None:
    row = ToolCallBlock('read_file', {})
    row.set_result('ok')
    header = ToolGroupHeader()
    header._rows = [row, row]
    console = Console()
    segments = list(console.render(header._build()))
    assert 'done' in ''.join(segment.text for segment in segments)
    assert not any(segment.style and segment.style.reverse for segment in segments)


@pytest.mark.parametrize('history', [False, True])
def test_should_separate_tools_and_style_user_messages(app: SigilApp, history: bool) -> None:
    async def scenario() -> None:
        async with app.run_test(size=(100, 35)) as pilot:
            chat = app.query_one(ChatLog)
            if history:
                await chat.render_history(
                    [
                        ModelRequest(parts=[UserPromptPart('question')]),
                        ModelResponse(parts=[TextPart('Before tools')]),
                        ModelResponse(parts=[ToolCallPart('read_file', {'path': 'a'}, tool_call_id='a')]),
                        ModelRequest(parts=[ToolReturnPart('read_file', 'ok', tool_call_id='a')]),
                    ]
                )
            else:
                await chat.add_user('question')
                await chat.add_text('Before tools')
                await chat.add_tool_call('read_file', {'path': 'a'})
            await pilot.pause()
            user = chat.query_one(UserMessage)
            assert user.styles.background.a > 0
            assert user.styles.border_left[0] != ''
            row = chat.query_one(ToolCallBlock)
            assert row.styles.margin.top == 1
            if not history:
                await chat.add_tool_call('read_file', {'path': 'b'})
                await pilot.pause()
                assert chat.query_one(ToolGroupHeader).styles.margin.top == 1
                assert all(row.styles.margin.top == 0 for row in chat.query(ToolCallBlock))

    asyncio.run(scenario())


def test_should_recall_message_with_attachments_only_when_empty(app: SigilApp) -> None:
    async def scenario() -> None:
        async with app.run_test() as pilot:
            field = app.query_one(PromptInput)
            await pilot.press('up')
            assert field.value == ''
            field.value = 'first\nsecond '
            image = field.attach(ImageAttachment(number=1, data=b'image', media_type='image/png', width=1, height=1))
            sent = field.value
            await field.action_submit()
            await pilot.pause()
            field.focus()
            await pilot.press('up')
            assert field.value == sent
            assert field.attachments == (image,)
            assert field.cursor_location == field.document.end
            field.value = 'draft\nline'
            await pilot.press('up')
            assert field.value == 'draft\nline'
            assert field.cursor_location[0] == 0
            await pilot.press('down')
            assert field.cursor_location[0] == 1

    asyncio.run(scenario())


def test_should_recall_command_selected_from_menu(app: SigilApp) -> None:
    async def scenario() -> None:
        async with app.run_test() as pilot:
            field = app.query_one(PromptInput)
            field.value = '/hel'
            await pilot.pause()
            assert app.menu_visible
            await pilot.press('enter')
            await pilot.press('up')
            assert field.value == '/help'
            await pilot.pause()
            assert app.menu_visible
            await pilot.press('down')
            assert field.value == '/help'

    asyncio.run(scenario())
