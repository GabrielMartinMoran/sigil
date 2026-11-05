"""The questions inside the real app: the chat stays visible while asking.

The point of the panel is that the question does not cover the conversation: the
chat gives up part of its height, and what was already written there is still
readable while answering.
"""

import asyncio
import tempfile
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel
from textual.widgets import OptionList, Static

from src.engine.interaction.hub import QuestionHub
from src.engine.interaction.models.question_item import QuestionItem, QuestionOption
from src.engine.interaction.tools import register_interaction_tools
from src.engine.sessions.session_store import SessionStore
from src.ui.app import SigilApp
from src.ui.widgets.chat_log import ChatLog
from src.ui.widgets.prompt_input import PromptInput
from src.ui.widgets.question_panel import QuestionPanel


def _question(text: str = 'What do we do?') -> QuestionItem:
    return QuestionItem(
        question=text,
        options=[
            QuestionOption(label='Recommended', recommended=True),
            QuestionOption(label='Alternative'),
        ],
    )


def _make_app(tmp: str, hub: QuestionHub) -> SigilApp:
    store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
    return SigilApp(
        agent=Agent(TestModel(custom_output_text='ok'), instructions='x'),
        store=store,
        session=store.create_session(),
        question_hub=hub,
    )


async def _answer(pilot, prompts: dict[str, str]) -> None:
    """Answers the questions the hub registered, one enter per question."""
    for _ in prompts:
        await pilot.press('enter')
        await pilot.pause()


def test_panel_does_not_cover_the_chat():

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _make_app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                sidebar = app.query_one('#sidebar')
                for index in range(30):
                    await chat.add_user(f'message {index}')
                await pilot.pause()
                height_without_panel = chat.size.height
                sidebar_height = sidebar.size.height

                task = asyncio.create_task(app.ask_question([_question('First'), _question('Second')]))
                await pilot.pause()
                await pilot.pause()

                panel = app.query_one(QuestionPanel)
                assert panel.display is True
                # The chat shrinks instead of disappearing: what is above stays visible.
                assert chat.display is True
                assert chat.size.height < height_without_panel
                assert chat.size.height >= 5
                # Only the conversation gives up room: the sidebar does not move.
                assert sidebar.size.height == sidebar_height
                # And the panel sits under the chat, above the prompt.
                prompt = app.query_one('#prompt')
                assert panel.region.y >= chat.region.bottom - 1
                assert panel.region.y + panel.region.height <= prompt.region.y

                await _answer(pilot, {'First': '', 'Second': ''})
                answers = await asyncio.wait_for(task, timeout=5)
                assert answers is not None
                assert [answer.answer for answer in answers] == ['Recommended', 'Recommended']
                assert panel.display is False
                # The chat takes its space back.
                await pilot.pause()
                assert chat.size.height == height_without_panel

    asyncio.run(inner())


def test_hub_reaches_the_panel_and_the_answer_comes_back():

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _make_app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                task = asyncio.create_task(hub.ask([_question()]))
                await pilot.pause()
                await pilot.pause()

                # Choose the second option, then confirm.
                await pilot.press('down')
                await pilot.press('enter')
                answers = await asyncio.wait_for(task, timeout=5)

                assert answers is not None
                assert answers[0].answer == 'Alternative'

    asyncio.run(inner())


def test_escape_cancels_without_touching_the_conversation():

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _make_app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                await app.query_one(ChatLog).add_user('before')
                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()

                await pilot.press('escape')
                assert await asyncio.wait_for(task, timeout=5) is None
                assert app.query_one(QuestionPanel).display is False
                # The prompt gets the focus back: typing a message works again.
                assert app.focused is app.query_one('#prompt')
                await pilot.press('h', 'i')
                assert app.query_one(PromptInput).value == 'hi'

    asyncio.run(inner())


def test_a_tool_can_ask_mid_turn_and_the_turn_continues():

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            seen: list[str] = []
            agent = Agent(TestModel(custom_output_text='done'), instructions='x')

            @agent.tool_plain
            async def decide() -> str:
                answers = await hub.ask([_question('Mid turn?')])
                seen.append(answers[0].answer if answers else 'cancelled')
                return 'ok'

            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(agent=agent, store=store, session=store.create_session(), question_hub=hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.run_turn('please decide')
                await pilot.pause()
                await pilot.pause()

                # The panel opens while the turn waits, and the chat is still there
                # with what the agent has shown so far.
                panel = app.query_one(QuestionPanel)
                assert panel.display is True
                assert app.query_one(ChatLog).display is True
                assert app._turn_active is True

                await pilot.press('enter')
                for _ in range(40):
                    await pilot.pause()
                    if not app._turn_active:
                        break

                assert seen == ['Recommended']
                assert panel.display is False
                assert app._turn_active is False

    asyncio.run(inner())


def test_escaping_the_panel_stops_the_turn_and_gives_control_back():

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            agent = Agent(TestModel(custom_output_text='done'), instructions='x')
            register_interaction_tools(agent, hub)

            store = SessionStore(cwd=tmp, base_dir=Path(tmp) / 'sessions')
            app = SigilApp(agent=agent, store=store, session=store.create_session(), question_hub=hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                app.run_turn('ask me something')
                for _ in range(40):
                    await pilot.pause()
                    if app.query_one(QuestionPanel).display:
                        break
                assert app.query_one(QuestionPanel).display is True

                await pilot.press('escape')
                for _ in range(40):
                    await pilot.pause()
                    if not app._turn_active:
                        break

                # The turn is over and the prompt is usable again: the panel took
                # the whole turn with it, not just the question.
                assert app.query_one(QuestionPanel).display is False
                assert app._turn_active is False
                assert app.focused is app.query_one('#prompt')
                texts = [str(widget.render()) for widget in app.query_one(ChatLog).children]
                # The notice is only written when the run was cancelled: it is
                # the difference between stopping the turn and letting it go on.
                assert any('Turn cancelled.' in text for text in texts)
                # No error surfaced: cancelling is not a failure.
                assert not any('Error' in text for text in texts)

                # The conversation can go on.
                await pilot.press('h', 'i')
                await pilot.press('enter')
                for _ in range(40):
                    await pilot.pause()
                    if not app._turn_active:
                        break
                texts = [str(w.render()) for w in app.query_one(ChatLog).children]
                assert app._turn_active is False
                assert not any('Error' in text for text in texts)
                assert any('hi' in text for text in texts)

    asyncio.run(inner())


def test_ctrl_arrows_read_the_chat_without_leaving_the_panel():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _make_app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                for index in range(60):
                    await chat.add_user(f'message {index}')
                await pilot.pause()

                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()
                assert chat.is_vertical_scroll_end

                before = chat.scroll_offset.y
                await pilot.press('ctrl+up')
                await pilot.pause()
                assert chat.scroll_offset.y < before
                # The question does not move: the list still has the highlight.
                assert app.query_one(QuestionPanel).query_one(OptionList).highlighted == 0
                assert not chat.is_vertical_scroll_end

                await pilot.press('ctrl+down')
                await pilot.pause()
                assert chat.scroll_offset.y > before - 5

                await pilot.press('escape')
                assert await asyncio.wait_for(task, timeout=5) is None

    asyncio.run(inner())


def test_ctrl_arrows_read_the_debug_view_when_it_is_the_one_on_screen():
    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _make_app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                chat = app.query_one(ChatLog)
                for index in range(30):
                    await chat.add_user(f'message {index}')
                await pilot.pause()
                app.set_debug_visible(True)
                await pilot.pause()
                assert app.query_one(ChatLog).display is False

                task = asyncio.create_task(app.ask_question([_question()]))
                await pilot.pause()
                await pilot.pause()
                # The chat is not on screen: the keys must not move a hidden view.
                chat_offset = chat.scroll_offset.y
                await pilot.press('ctrl+up')
                await pilot.pause()
                assert chat.scroll_offset.y == chat_offset

                await pilot.press('escape')
                assert await asyncio.wait_for(task, timeout=5) is None

    asyncio.run(inner())


def test_answered_questions_are_summarised_in_the_panel():

    async def inner():
        with tempfile.TemporaryDirectory() as tmp:
            hub = QuestionHub()
            app = _make_app(tmp, hub)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                task = asyncio.create_task(app.ask_question([_question('First'), _question('Second')]))
                await pilot.pause()
                await pilot.pause()

                panel = app.query_one(QuestionPanel)
                await pilot.press('enter')  # First: Recommended
                await pilot.pause()
                assert panel.query_one('#question-title', Static).render().plain == 'Second'

                await pilot.press('alt+left')
                await pilot.pause()
                assert panel.query_one('#question-title', Static).render().plain == 'First'
                summary = panel.query_one('#question-answers', Static).render().plain
                assert 'First' in summary
                assert 'Recommended' in summary
                options = panel.query_one(OptionList)
                assert options.highlighted == 0

                await pilot.press('alt+right')
                await pilot.pause()
                await pilot.press('enter')
                answers = await asyncio.wait_for(task, timeout=5)
                assert answers is not None
                assert len(answers) == 2

    asyncio.run(inner())
